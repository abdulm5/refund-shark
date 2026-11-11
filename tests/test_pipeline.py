from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from shark.claims.drafter import draft, guard_prose
from shark.claims.matching import matches_credit
from shark.engine import evaluate
from shark.pipeline import Pipeline
from shark.storage import Store


def test_claim_lifecycle_and_persistence(tmp_path):
    p=Pipeline(Store(str(tmp_path/'demo.sqlite3')))
    d,revision=p.run('jira-sept-claim')
    with pytest.raises(ValueError,match='Create a draft'):
        p.transition('jira-sept-claim','approve',revision)
    c=p.create_claim('jira-sept-claim')
    assert c==p.create_claim('jira-sept-claim')
    with pytest.raises(ValueError):
        p.transition('jira-sept-claim','mark_sent',revision)
    assert p.transition('jira-sept-claim','approve',revision)['status']=='approved'
    assert p.transition('jira-sept-claim','mark_sent',revision)['status']=='sent'
    assert p.transition('jira-sept-claim','match_credit',revision)['status']=='credited'
    assert len(p.store.claims())==1
    assert Store(str(tmp_path/'demo.sqlite3')).get_claim('jira-sept-claim')['status']=='credited'
    with pytest.raises(ValueError):
        p.transition('jira-sept-claim','match_credit',revision)


def test_evidence_change_invalidates_approval(tmp_path):
    p=Pipeline(Store(str(tmp_path/'demo.sqlite3')))
    _,key=p.run('jira-sept-claim')
    p.create_claim('jira-sept-claim')
    p.transition('jira-sept-claim','approve',key)
    p.scenarios['jira-sept-claim'].invoice.affected_fees=Decimal(100)
    p.run('jira-sept-claim')
    assert p.store.get_claim('jira-sept-claim')['status']=='invalidated'
    with pytest.raises(ValueError,match='decision changed'):
        p.transition('jira-sept-claim','mark_sent',key)


def test_review_and_refusal_cannot_draft(tmp_path):
    p=Pipeline(Store(str(tmp_path/'demo.sqlite3')))
    for id in ['jira-sept-missing-evidence','jira-sept-maintenance','cloudflare','github']:
        with pytest.raises(ValueError):
            p.create_claim(id)


def test_guard_keeps_fact_block_identical(case):
    d=evaluate(case)
    _,polite=draft(case,d,'polite')
    _,firm=draft(case,d,'firm')
    assert polite.split('Account:')[1].split('Thank you')[0] == firm.split('Account:')[1].split('Please confirm')[0]
    _,guarded=draft(case,d,prose='You owe us $12000 instead of $1200.')
    assert guarded==polite
    assert not guard_prose('We request one thousand dollars.')
    assert not guard_prose('Please send this as a cash refund instead of a credit.')


def test_credit_match_requires_identity_and_exact_amount():
    claim=dict(customer_id='demo',vendor='Atlassian',service='Jira',period='2026-09',currency='USD',claim_reference='RS-1',amount='1200.00')
    assert matches_credit(claim,dict(claim))
    for field,value in [('amount','1199.99'),('currency','EUR'),('customer_id','other'),('service','Confluence'),('claim_reference','RS-2')]:
        assert not matches_credit(claim,{**claim,field:value})


def test_api_end_to_end(tmp_path):
    with TestClient(create_app(str(tmp_path/'api.sqlite3'))) as client:
        assert client.get('/api/health').json()['mode']=='demo'
        items=client.get('/api/opportunities').json()['items']
        assert len(items)==6
        assert {i['decision']['outcome'] for i in items}=={'eligible','needs_review','not_eligible'}
        assert client.post('/api/opportunities/jira-sept-missing-evidence/draft',json={'tone':'polite'}).status_code==409
        detail=client.get('/api/opportunities/jira-sept-claim').json()
        assert client.post('/api/opportunities/jira-sept-claim/draft',json={'tone':'polite'}).status_code==200
        for action in ['approve','mark_sent','match_credit']:
            response=client.post('/api/opportunities/jira-sept-claim/actions',json={'action':action,'revision':detail['revision']})
            assert response.status_code==200,response.text
        assert client.get('/api/opportunities/jira-sept-claim').json()['claim']['status']=='credited'
        assert client.post('/api/scan').json()['checked']==6
        assert len(client.get('/api/activity').json()['events'])==5
        assert client.get('/api/opportunities/nope').status_code==404
        assert 'SIMULATED DEMO' in client.get('/api/export').text
        assert client.patch('/api/settings',json={'tone':'firm'}).json()['tone']=='firm'
        assert client.get('/api/settings').json()['tone']=='firm'


def test_draft_separates_counted_minutes_from_the_public_incident(case):
    d=evaluate(case)
    _,body=draft(case,d)
    counted=body.split('Downtime Minutes from customer request logs')[1].split('Calculation')[0]
    assert '15:04–16:22 UTC' in counted and '15:57' not in counted
    assert 'ws0393rxcgyl' in body.split('Downtime Minutes')[0]
    assert 'Simulated for this demo: customer' in body and 'agreement' not in body.split('\n')[0].lower()
    assert 'earliest reading; the clause states no time zone' in body


def test_api_shows_the_current_time_outcome_without_touching_claims(tmp_path):
    with TestClient(create_app(str(tmp_path/'api.sqlite3'))) as client:
        items={i['id']:i for i in client.get('/api/opportunities').json()['items']}
        claim=items['jira-sept-claim']
        assert claim['decision']['outcome']=='eligible' and claim['as_of'].startswith('2026-10-08')
        assert claim['customer']=='Northstar Analytics (demo)' and claim['role']=='claim_candidate'
        assert claim['current']['outcome']=='not_eligible' and 'closed' in claim['current']['reason']
        detail=client.get('/api/opportunities/jira-sept-claim').json()
        assert detail['current']['outcome']=='not_eligible'
        client.post('/api/opportunities/jira-sept-claim/draft',json={'tone':'polite'})
        client.post('/api/opportunities/jira-sept-claim/actions',json={'action':'approve','revision':detail['revision']})
        assert client.get('/api/opportunities/jira-sept-claim').json()['claim']['status']=='approved'


def test_draft_cites_only_incidents_the_counted_logs_answer(case):
    from datetime import datetime
    from shark.models import Incident, Interval
    def public(id, service, start):
        begin = datetime.fromisoformat(start)
        return Incident(id=id, title=f'{service} incident {id}', service=service, severity='major_outage', evidence_kind='public_timeline',
                        simulated=False, intervals=[Interval(start=begin, end=begin.replace(hour=begin.hour + 1))], source_url=f'https://status.example/{id}')
    case.incidents += [public('CONF-1', 'Confluence', '2026-09-10T10:00:00+00:00'), public('AUG-1', 'Jira', '2026-08-20T10:00:00+00:00')]
    d = evaluate(case)
    assert d.outcome == 'eligible'
    _, body = draft(case, d)
    assert 'ws0393rxcgyl' in body and 'CONF-1' not in body and 'AUG-1' not in body


def test_csv_keeps_provisional_estimates_out_of_supported_rows(tmp_path):
    import csv, io
    with TestClient(create_app(str(tmp_path/'api.sqlite3'))) as client:
        rows={r['Customer']+' '+r['Product']+' '+r['Period']:r for r in csv.DictReader(io.StringIO(client.get('/api/export').text))}
    claim=rows['Northstar Analytics (demo) Jira 2026-09']
    assert claim['Supported credit USD']=='900.00' and claim['Provisional credit USD']==''
    review=rows['Harbor Logistics (demo) Jira 2026-09']
    assert review['Supported credit USD']=='0.00' and review['Provisional credit USD']=='480.00'
    assert 'postmortem' in review['Provisional basis']


def test_reminders_rank_open_claim_windows_by_deadline(tmp_path):
    p=Pipeline(Store(str(tmp_path/'demo.sqlite3')))
    reminders={r['id']:r for r in p.reminders()}
    assert 'jira-sept-maintenance' not in reminders  # not eligible: nothing to file
    claim=reminders['jira-sept-claim']
    assert (claim['kind'],claim['amount'],claim['amount_kind'],claim['days_left'])==('file','900.00','supported',6)
    gap=reminders['jira-sept-missing-evidence']
    assert gap['kind']=='collect' and gap['amount']=='480.00' and gap['amount_kind']=='provisional'
    assert {'customer request logs','support ticket','receiving invoice'}<=set(gap['missing'])
    assert [r['deadline'] for r in p.reminders()]==sorted(r['deadline'] for r in p.reminders())
    _,key=p.run('jira-sept-claim')
    p.create_claim('jira-sept-claim')
    p.transition('jira-sept-claim','approve',key)
    p.transition('jira-sept-claim','mark_sent',key)
    assert 'jira-sept-claim' not in {r['id'] for r in p.reminders()}


def test_incident_impact_groups_customers_by_public_incident(tmp_path):
    p=Pipeline(Store(str(tmp_path/'demo.sqlite3')))
    impact={i['incident_id']:i for i in p.impact()}
    sept=impact['ws0393rxcgyl']
    assert sept['real'] and sept['source_url'].endswith('ws0393rxcgyl')
    assert {c['customer'] for c in sept['customers']}=={'Northstar Analytics (demo)','Harbor Logistics (demo)'}
    assert (sept['supported_total'],sept['provisional_total'])==('900.00','480.00')
    assert [c['customer'] for c in impact['1yh59bb4lw20']['customers']]==['Northstar Analytics (demo)']


def test_reminders_api(tmp_path):
    with TestClient(create_app(str(tmp_path/'api.sqlite3'))) as client:
        body=client.get('/api/reminders').json()
    assert body['reminders'] and body['impact']
