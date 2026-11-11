import json
from pathlib import Path

import pytest

from shark.extract.citations import normalize, source_hash, term_status, verify_citations
from shark.extract.provider import extract, replay
from shark.incidents.statuspage import normalize_incident
from shark.pipeline import ROOT


def test_normalized_quotes(case):
    assert normalize('“hello”\n  world')=='"hello" world'
    assert set(term_status(case.terms,case.source_text).values())=={'verified','absent'}
    assert not any(verify_citations(case.terms,'completely unrelated').values())


def test_replay_requires_exact_snapshot(case,tmp_path):
    saved=tmp_path/'output.json'
    saved.write_text(json.dumps({'source_hash':case.source_hash,'terms':case.terms.model_dump(mode='json')}))
    assert replay(saved,case.source_text)['mode']=='replay'
    with pytest.raises(ValueError,match='hash'):
        replay(saved,case.source_text+'Changed')


def test_model_key_absence_is_clear(monkeypatch,tmp_path):
    monkeypatch.delenv('ANTHROPIC_API_KEY',raising=False)
    with pytest.raises(ValueError,match='ANTHROPIC_API_KEY'):
        extract('test','test','test',tmp_path)


class FakeMessages:
    """Stands in for client.beta.messages; answers each group request from the given terms."""
    def __init__(self, terms, model='claude-sonnet-5-5', stop_reason='end_turn'):
        self.calls, self.terms, self.model, self.stop_reason = [], terms, model, stop_reason

    def parse(self, **kwargs):
        from types import SimpleNamespace
        from shark.extract.schema import from_terms
        self.calls.append(kwargs)
        usage = SimpleNamespace(input_tokens=100, output_tokens=100)
        group = kwargs['output_format']
        parsed = group.model_validate(from_terms(self.terms).model_dump(include=set(group.model_fields))) if self.terms is not None else None
        return SimpleNamespace(stop_reason=self.stop_reason, parsed_output=parsed, model=self.model, usage=usage)


def fake_client(messages):
    from types import SimpleNamespace
    return SimpleNamespace(beta=SimpleNamespace(messages=messages))


def test_model_extraction_contract_and_cache(monkeypatch,tmp_path,case):
    from shark.models import SLATerms
    monkeypatch.setenv('ANTHROPIC_API_KEY','test-key-not-real')
    messages=FakeMessages(case.terms)
    first=extract(case.source_text,'Jira','Premium',tmp_path,client=fake_client(messages))
    from shark.extract.schema import GROUPS
    assert [c['output_format'] for c in messages.calls]==list(GROUPS.values())  # three schemas small enough to compile
    assert SLATerms.model_validate(first['terms'])==case.terms and first['usage']=={'input_tokens':300,'output_tokens':300}
    assert set(first['raw_output'])=={'commitment','pricing','process'}  # kept for audit
    request=messages.calls[0]
    assert request['fallbacks']=='default' and 'server-side-fallback-2026-07-01' in request['betas']
    assert '<source>' in request['messages'][0]['content'] and 'untrusted' in request['system']
    assert first['mode']=='live' and first['served_by']=='claude-sonnet-5-5'
    assert extract(case.source_text,'Jira','Premium',tmp_path,client=fake_client(messages))['mode']=='replay'
    assert len(messages.calls)==3


def test_extraction_refusal_is_reported(monkeypatch,tmp_path,case):
    monkeypatch.setenv('ANTHROPIC_API_KEY','test-key-not-real')
    with pytest.raises(ValueError,match='refusal'):
        extract(case.source_text,'Jira','Premium',tmp_path,client=fake_client(FakeMessages(None,stop_reason='refusal')))
    assert not list(tmp_path.iterdir())


def test_real_incident_normalization():
    incident=json.loads((ROOT/'docs/research/real-incidents.json').read_text())['incidents'][0]
    result=normalize_incident(incident,{'b01bgtvb4q2j'},'Jira')
    assert len(result.intervals)==1
    assert result.intervals[0].start.isoformat().startswith('2026-09-03T15:57:31.948')
    assert result.intervals[0].end.isoformat().startswith('2026-09-03T16:35:58')
    assert not result.metric_verified and not result.simulated


def test_reopening_and_reverse_order():
    updates=[{'id':str(index),'created_at':f'2026-09-01T10:{minute:02}:00Z','status':status,'components':[{'code':'one','new_status':component}]} for index,(minute,status,component) in enumerate([(0,'investigating','major_outage'),(10,'monitoring','operational'),(20,'identified','major_outage'),(30,'resolved','operational')])]
    record={'id':'real','name':'Reopened incident','incident_updates':list(reversed(updates))}
    result=normalize_incident(record,{'one'},'Jira')
    assert len(result.intervals)==2
    assert sum((i.end-i.start).total_seconds() for i in result.intervals)==1200


def test_excerpt_lines_must_come_from_the_snapshot():
    from shark.extract.excerpts import missing_excerpt_lines
    snapshot = 'Line one\nLine two\nLine three\n'
    assert missing_excerpt_lines('Line one\n[…]\nLine three\n', snapshot) == []
    assert missing_excerpt_lines('Line one\nInvented line\n', snapshot) == ['Invented line']


def test_snapshot_records_raw_and_text_hashes(tmp_path):
    import hashlib
    from datetime import datetime, timezone
    from shark.extract.fetch import save_snapshot
    paragraph = 'Customer must submit a ticket within fifteen (15) days after the end of the calendar month. ' * 4
    html = f'<html><body><article><h1>Service Level Agreement</h1><p>Effective starting: April 30, 2026</p><p>{paragraph}</p></article></body></html>'.encode()
    meta = save_snapshot(html, 'text/html', 'https://example.com/sla', tmp_path, 'example', datetime(2026, 10, 30, tzinfo=timezone.utc))
    assert meta['raw_sha256'] == hashlib.sha256(html).hexdigest()
    assert meta['effective_date'] == '2026-04-30'
    text = Path(meta['text_path']).read_text()
    assert 'fifteen (15) days' in text and meta['source_hash'] == source_hash(text)


def test_committed_excerpts_are_intact():
    from shark.extract.excerpts import load_manifest
    for ref in load_manifest()['sources']:
        assert source_hash((ROOT / ref['excerpt_path']).read_text()) == ref['excerpt_sha256'], ref['id']


def test_committed_excerpts_match_cached_snapshots():
    from shark.extract.excerpts import load_manifest, missing_excerpt_lines
    from shark.extract.fetch import html_to_text
    import hashlib
    checked = 0
    for ref in load_manifest()['sources']:
        raw = ROOT / ref['cache_path']
        if not raw.exists():
            continue
        assert hashlib.sha256(raw.read_bytes()).hexdigest() == ref['raw_sha256']
        text = html_to_text(raw.read_text())
        assert source_hash(text) == ref['text_sha256']
        assert missing_excerpt_lines((ROOT / ref['excerpt_path']).read_text(), text) == []
        checked += 1
    if not checked:
        pytest.skip('Raw vendor snapshots are not cached; run make fetch-sources to verify excerpts')


def test_degraded_incident_keeps_its_window_and_severity():
    incident=next(i for i in json.loads((ROOT/'docs/research/real-incidents.json').read_text())['incidents'] if i['id']=='1yh59bb4lw20')
    result=normalize_incident({**incident,'incident_updates':incident['updates']},{'jmgfbtr0k28j'},'Jira')
    assert result.severity=='degraded_performance'
    assert [(i.start.isoformat()[:19],i.end.isoformat()[:19]) for i in result.intervals]==[('2026-10-21T14:28:49','2026-10-21T15:01:51')]
    assert 'degraded performance' in result.interval_basis


def test_out_of_scope_components_are_ignored():
    incident=json.loads((ROOT/'docs/research/real-incidents.json').read_text())['incidents'][0]
    result=normalize_incident(incident,{'not-a-component'},'Jira')
    assert result.intervals==[] and result.severity=='operational'


def test_open_incident_keeps_its_start_and_no_closed_window():
    updates=[{'id':'1','created_at':'2026-09-01T10:00:00Z','status':'investigating','components':[{'code':'one','new_status':'major_outage'}]}]
    result=normalize_incident({'id':'open','name':'Open incident','incident_updates':updates},{'one'},'Jira')
    assert result.intervals==[] and result.severity=='major_outage'
    assert result.started_at.isoformat().startswith('2026-09-01T10:00')


def test_repeated_runs_are_independent_calls(monkeypatch,tmp_path,case):
    monkeypatch.setenv('ANTHROPIC_API_KEY','test-key-not-real')
    messages=FakeMessages(case.terms)
    first=extract(case.source_text,'Jira','Premium',tmp_path,client=fake_client(messages),run=1)
    second=extract(case.source_text,'Jira','Premium',tmp_path,client=fake_client(messages),run=2)
    assert len(messages.calls)==6 and (first['run'],second['run'])==(1,2)
    assert messages.calls[0]['output_config']=={'effort':'medium'} and first['effort']=='medium'
    assert extract(case.source_text,'Jira','Premium',tmp_path,client=fake_client(messages),run=2)['mode']=='replay'
