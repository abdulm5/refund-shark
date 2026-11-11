"""Behavior checks for reviewed contracts, monthly cases, incidents, Datadog, and isolated replay."""
import copy
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from shark.models import Scenario
from shark.monitoring import Monitoring, MonitoringConfig, SyncWindow, parse_response, query_payload
from shark.monthly import merge_logs
from shark.pipeline import Pipeline
from test_recovery import client, setup, confirm, evidence, sent_claim


def review_body(read):
    return {"terms": copy.deepcopy(read["terms"]), "reviewer": "Finance reviewer", "reason": "Checked every term against the saved clauses", "confirmed": True}


def case_form(client, vendor, read):
    log = evidence(client, vendor)
    ticket = evidence(client, vendor, "ticket")
    credit = confirm(client, "credit")
    return {"evidence_ids": [log["id"]], "ticket_ids": [ticket["id"]], "receiving_invoice_id": credit["id"],
        "as_of": "2026-10-08T12:00:00Z", "prerequisites": {p: True for p in read["case_prerequisites"]}, "exclusions_reviewed": True}


def test_review_preserves_original_and_links_only_when_chosen(client):
    v, read = setup(client)
    store = client.app.state.pipeline.store
    original = store.get_read(read["read_id"])
    response = client.post(f"/api/agreements/reads/{read['read_id']}/approve", json=review_body(read))
    assert response.status_code == 200, response.text
    reviewed = response.json()
    assert reviewed["review"]["version"] == 1 and reviewed["review"]["changed_fields"] == []
    assert store.get_read(read["read_id"]) == original
    assert store.record("vendor", v["id"])["read_id"] == read["read_id"]
    assert len(reviewed["history"]) == 2
    assert client.post(f"/api/agreements/reads/{read['read_id']}/approve", json=review_body(read)).status_code == 422
    body = review_body(read)
    body["terms"]["claim_window"]["value"]["value"] = 14
    body["reason"] = "Reviewer interpretation correction; source quote remains pinned"
    second = client.post(f"/api/agreements/reads/{reviewed['id']}/approve", json=body).json()
    assert second["review"]["version"] == 2
    assert second["review"]["changed_fields"] == ["claim_window"]
    assert store.get_read(reviewed["id"])["terms"]["claim_window"]["value"]["value"] != 14


@pytest.mark.parametrize("change", ["quote", "overlap", "plan", "reason", "confirmation"])
def test_bad_reviews_are_refused(client, change):
    _, read = setup(client)
    body = review_body(read)
    if change == "quote": body["terms"]["claim_window"]["quote"] = "this quote is not present in the source"
    if change == "overlap": body["terms"]["credit_tiers"]["value"].append(body["terms"]["credit_tiers"]["value"][0])
    if change == "plan": body["terms"]["plan"]["value"] = "Wrong plan"
    if change == "reason": body["reason"] = ""
    if change == "confirmation": body["confirmed"] = False
    r = client.post(f"/api/agreements/reads/{read['read_id']}/approve", json=body)
    assert r.status_code == 422, r.text
    assert len(client.app.state.pipeline.store.reads()) == 1


def test_review_unknown_cannot_be_turned_into_verified_value_without_quote(client):
    _, read = setup(client)
    body = review_body(read)
    body["terms"]["claim_window"] = {"value": None, "quote": None, "section": None, "absent": False, "missing_reason": "Ambiguous deadline wording"}
    r = client.post(f"/api/agreements/reads/{read['read_id']}/approve", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["term_status"]["claim_window"] == "missing"
    assert not r.json()["readiness"]["can_price"]


def test_review_lineage_serializes_competing_approvals(client):
    _, read = setup(client)
    from shark.reviews import approve_review, ReviewInput
    store = client.app.state.pipeline.store
    def approve(_):
        try:
            return approve_review(store, read["read_id"], ReviewInput(**review_body(read)))["id"]
        except ValueError:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        values = list(pool.map(approve, range(2)))
    assert sum(v is not None for v in values) == 1


def test_monthly_idempotence_explicit_update_and_approval_invalidation(client):
    v, read = setup(client)
    form = case_form(client, v, read)
    path = f"/api/vendors/{v['id']}/cases"
    initial = client.post(path, json=form).json()
    again = client.post(path, json=form).json()
    assert again["id"] == initial["id"] and again["reused"]
    assert again["decision"]["amount"] == "900.00"
    client.post(f"/api/opportunities/{initial['id']}/draft", json={"tone": "polite"})
    client.post(f"/api/opportunities/{initial['id']}/actions", json={"action": "approve", "revision": initial["revision"]})
    changed = form | {"ticket_ids": []}
    assert client.post(path, json=changed).status_code == 422
    updated = client.post(path, json=changed | {"confirm_update": True, "expected_revision": initial["revision"]}).json()
    assert updated["id"] == initial["id"] and updated["decision"]["outcome"] == "needs_review"
    assert updated["claim"]["status"] == "invalidated"
    history = client.get(f"/api/cases/{initial['id']}/history").json()["revisions"]
    assert len(history) == 1 and history[0]["claim"]["status"] == "approved"
    assert len([s for s in client.app.state.pipeline.scenarios.values() if s.role == "user_case"]) == 1


def test_multiple_logs_deduplicate_counts_and_conflicts_refuse(client):
    v, read = setup(client)
    form = case_form(client, v, read)
    duplicate = evidence(client, v, label="Same export, overlapping upload")
    form["evidence_ids"].append(duplicate["id"])
    r = client.post(f"/api/vendors/{v['id']}/cases", json=form)
    assert r.status_code == 200, r.text
    assert r.json()["decision"]["downtime_minutes"] == "62"
    assert "identical overlapping rows removed" in json.dumps(r.json()["scenario"]["provenance"])
    store = client.app.state.pipeline.store
    text = "minute,experience,requests,errors\n2026-09-03T15:00:00Z,View Issue,100,10\n"
    a = evidence(client, v, label="A", csv=text)
    b = evidence(client, v, label="B", csv=text.replace(",100,10", ",100,11"))
    with pytest.raises(ValueError, match="Conflicting counts"):
        merge_logs(store, [a, b], "2026-09")


def test_log_union_combines_distinct_outages_and_ticket_windows(client):
    v, _ = setup(client)
    text = "minute,experience,requests,errors\n2026-09-03T15:00:00Z,View Issue,100,10\n"
    a = evidence(client, v, label="first", csv=text)
    b = evidence(client, v, label="second", csv=text.replace("15:00", "18:00"))
    store = client.app.state.pipeline.store
    merged, stats = merge_logs(store, [a, b], "2026-09")
    assert stats["rows"] == 2 and "18:00" in merged
    ticket = evidence(client, v, "ticket")
    result = client.post(f"/api/vendors/{v['id']}/cases", json={"evidence_ids": [a["id"], b["id"]], "ticket_ids": [ticket["id"]], "as_of": "2026-10-08T12:00:00Z"}).json()
    assert result["scenario"]["account"]["prerequisites"]["support_ticket_during_incident"] is None


def test_submitted_month_cannot_be_rebuilt_deleted_or_duplicated(client):
    v, read = setup(client)
    form = case_form(client, v, read)
    path = f"/api/vendors/{v['id']}/cases"
    r = client.post(path, json=form).json()
    sent_claim(client, r["id"])
    assert client.post(path, json=form).json()["reused"]
    assert client.post(path, json=form | {"ticket_ids": [], "confirm_update": True, "expected_revision": r["revision"]}).status_code == 422
    assert client.delete(f"/api/cases/{r['id']}").status_code == 409
    copy_case = Scenario.model_validate(r["scenario"])
    copy_case.id = "second-attempt"
    with pytest.raises(ValueError, match="already covers"):
        client.app.state.pipeline.add_user_case(copy_case)
    # Even duplicate records saved by an older app cannot produce a second active claim.
    client.app.state.pipeline.scenarios[copy_case.id] = copy_case
    assert client.post(f"/api/opportunities/{copy_case.id}/draft", json={"tone": "polite"}).status_code == 409


def test_monthly_case_restores_and_rejects_foreign_evidence(client):
    v, read = setup(client)
    form = case_form(client, v, read)
    result = client.post(f"/api/vendors/{v['id']}/cases", json=form).json()
    store = client.app.state.pipeline.store
    restarted = Pipeline(store)
    from shark.monthly import existing_month
    assert existing_month(store, restarted, v["id"])["id"] == result["id"]
    form["evidence_ids"] = ["foreign-record"]
    form.update(confirm_update=True, expected_revision=result["revision"])
    assert client.post(f"/api/vendors/{v['id']}/cases", json=form).status_code == 422


def test_incident_action_captures_ticket_and_leaves_open_window_unknown(client):
    v, _ = setup(client)
    client.post("/api/watch/drill", json={"action": "start"})
    alert = next(a for a in client.get("/api/alerts").json()["alerts"] if a["customer_id"] == v["id"])
    panel = client.get(f"/api/alerts/{alert['id']}/action").json()
    assert panel["owner"] == "Finance" and panel["account_reference"] == v["account_reference"]
    assert panel["support_url"] == "https://support.atlassian.com/contact/"
    body = {"reference": "SUP-123", "opened_at": "2026-09-03T16:00:00Z", "incident_start": "2026-09-03T15:00:00Z", "confirmed": True}
    path = f"/api/alerts/{alert['id']}/ticket"
    r = client.post(path, json=body)
    assert r.status_code == 200, r.text
    capture = r.json()["capture"]
    assert capture["ticket_during_incident"] is None and capture["evidence_id"]
    r = client.post(path, json=body | {"incident_end": "2026-09-03T17:30:00Z"}).json()
    assert r["capture"]["ticket_during_incident"] is True
    assert r["steps"][1]["done"] and r["steps"][2]["done"]
    assert client.post(path, json=body | {"confirmed": False}).status_code == 422
    assert client.post(path, json=body | {"opened_at": "2099-01-01T00:00:00Z"}).status_code == 422


def mapping(read):
    return {"site": "datadoghq.com", "confirmed_count_metrics": True, "automatic": False,
        "mappings": [{"experience": e, "requests_metric": "customer.requests", "errors_metric": "customer.errors", "scope": f"account:demo,experience:{i}"}
                     for i, e in enumerate(read["terms"]["downtime_definition"]["value"]["covered_experiences"])]}


def datadog_payload(config, start, minutes=3):
    return {"data": {"type": "timeseries_response", "attributes": {
        "times": [int(start.timestamp()*1000)+i*60000 for i in range(minutes)],
        "series": [{"query_index": i, "group_tags": []} for i in range(len(config.mappings)*2)],
        "values": [[100, 100, 100][:minutes] if i%2==0 else [10, 0, 0][:minutes] for i in range(len(config.mappings)*2)]}}}


def test_datadog_request_uses_count_rollup_scope_and_fixed_host(client):
    _, read = setup(client)
    config = MonitoringConfig(**mapping(read))
    start = datetime(2026, 9, 3, 15, tzinfo=timezone.utc)
    payload = query_payload(config, start, start+timedelta(minutes=3))
    attrs = payload["data"]["attributes"]
    assert attrs["interval"] == 60000 and attrs["to"]-attrs["from"] == 180000
    assert all(".as_count().rollup(sum,60).fill(null)" in q["query"] and "account:demo" in q["query"] for q in attrs["queries"])
    with pytest.raises(ValueError): MonitoringConfig(**(mapping(read)|{"site":"attacker.example"}))


def test_datadog_gaps_not_zero_and_series_order_not_assumed(client):
    _, read = setup(client)
    config = MonitoringConfig(**mapping(read)); start = datetime(2026,9,3,15,tzinfo=timezone.utc)
    data = datadog_payload(config,start)
    attrs = data["data"]["attributes"]
    attrs["values"][1][1] = None
    attrs["series"].reverse(); attrs["values"].reverse()
    rows, gaps = parse_response(data,config,start,start+timedelta(minutes=3))
    assert gaps == 1 and len(rows) == 2*len(config.mappings)
    assert all(row[0].minute != 1 for row in rows)


@pytest.mark.parametrize("bad", ["coarse", "fraction", "negative", "errors", "duplicate", "missing", "unaligned", "array", "null"])
def test_datadog_invalid_counts_and_shapes_refuse(client,bad):
    _, read = setup(client)
    config=MonitoringConfig(**mapping(read));start=datetime(2026,9,3,15,tzinfo=timezone.utc)
    data=datadog_payload(config,start);attrs=data["data"]["attributes"]
    if bad=="coarse": attrs["times"][1]+=60000
    if bad=="fraction": attrs["values"][0][0]=3.5
    if bad=="negative": attrs["values"][0][0]=-1
    if bad=="errors": attrs["values"][1][0]=101
    if bad=="duplicate": attrs["series"][1]["query_index"]=0
    if bad=="missing": attrs["values"].pop();attrs["series"].pop()
    if bad=="unaligned": attrs["times"][0]+=1000
    if bad=="array": data=[]
    if bad=="null": data=None
    with pytest.raises(ValueError):parse_response(data,config,start,start+timedelta(minutes=3))


def mock_datadog(monkeypatch, handler):
    real=httpx.Client
    monkeypatch.setattr("shark.monitoring.httpx.Client",lambda **kw:real(transport=httpx.MockTransport(handler),**kw))
    monkeypatch.setenv("SHARK_DATADOG_API_KEY","test-secret-api")
    monkeypatch.setenv("SHARK_DATADOG_APP_KEY","test-secret-app")


def test_native_sync_imports_idempotently_and_keeps_secrets_on_server(client,monkeypatch):
    v,read=setup(client); config=MonitoringConfig(**mapping(read));start=datetime(2026,9,3,15,tzinfo=timezone.utc)
    path=f"/api/vendors/{v['id']}/monitoring"
    assert client.put(path,json=mapping(read)).status_code==200
    calls=[]
    def respond(request):
        assert request.url.host=="api.datadoghq.com" and request.headers["DD-API-KEY"]=="test-secret-api"
        assert request.url.path=="/api/v2/query/timeseries"
        calls.append(request)
        return httpx.Response(200,json=datadog_payload(config,start))
    mock_datadog(monkeypatch,respond)
    window={"start":start.isoformat(),"end":(start+timedelta(minutes=3)).isoformat()}
    first=client.post(path+"/sync",json=window)
    assert first.status_code==200 and first.json()["state"]=="success",first.text
    again=client.post(path+"/sync",json=window).json()
    assert again["evidence_ids"]==first.json()["evidence_ids"]
    records=client.get(f"/api/vendors/{v['id']}/evidence").json()["evidence"]
    assert len(records)==1 and records[0]["source"]=="datadog"
    assert records[0]["analysis"]["downtime_minutes"]==1
    status=client.get(path)
    assert "test-secret" not in status.text and status.json()["credentials_configured"]
    assert len(calls)==2


def test_sync_rate_limit_pauses_without_leaking_body_or_importing(client,monkeypatch):
    v,read=setup(client); path=f"/api/vendors/{v['id']}/monitoring"
    client.put(path,json=mapping(read)|{"automatic":True})
    calls=[]
    def fail(request):
        calls.append(request);return httpx.Response(429,json={"error":"secret response details"})
    mock_datadog(monkeypatch,fail)
    result=client.post(path+"/sync",json={"start":"2026-09-03T15:00:00Z","end":"2026-09-03T15:03:00Z"})
    assert result.json()["state"]=="error" and "429" in result.json()["error"]
    assert "secret response" not in result.text
    monitoring=Monitoring(client.app.state.pipeline.store,"");monitoring.tick()
    assert len(calls)==1 and not client.get(f"/api/vendors/{v['id']}/evidence").json()["evidence"]


def test_sync_requires_complete_mapping_credentials_and_closed_minutes(client,monkeypatch):
    v,read=setup(client); path=f"/api/vendors/{v['id']}/monitoring"
    bad=mapping(read);bad["mappings"].pop()
    assert client.put(path,json=bad).status_code==422
    client.put(path,json=mapping(read))
    monkeypatch.delenv("SHARK_DATADOG_API_KEY",raising=False);monkeypatch.delenv("SHARK_DATADOG_APP_KEY",raising=False)
    r=client.post(path+"/sync",json={"start":"2026-09-03T15:00:00Z","end":"2026-09-03T15:03:00Z"}).json()
    assert r["state"]=="error" and "Configure" in r["error"]
    with pytest.raises(ValueError):SyncWindow(start="2099-01-01T00:00:00Z",end="2099-01-01T00:03:00Z")


def test_guided_demo_runs_real_engine_and_reset_preserves_workspace(client,monkeypatch):
    v,read=setup(client)
    before=client.app.state.pipeline.store.record("vendor",v["id"])
    # Any network attempt would fail this test; all steps must work offline.
    monkeypatch.setattr("httpx.Client.post",lambda *a,**k: (_ for _ in ()).throw(AssertionError("external call")))
    # TestClient.post uses its own implementation, while connector calls would use the patched base client method.
    for step in range(5):
        r=client.post("/api/demo/walkthrough/advance",json={"expected_step":step})
        assert r.status_code==200,r.text
        assert r.json()["step"]==step+1
    data=r.json()
    assert data["decision"]["downtime_minutes"]=="62" and data["decision"]["amount"]=="900.00"
    assert data["reconciliation"]["outcome"]=="matched"
    assert data["claim"]["id"]==data["reconciliation"]["claim_reference"]
    assert client.app.state.pipeline.store.get_claim("jira-sept-claim") is None
    assert client.app.state.pipeline.store.record("vendor",v["id"])==before
    reset=client.post("/api/demo/walkthrough/reset").json()
    assert reset["step"]==0 and reset["run_id"]!=data["run_id"]
    assert client.get("/api/vendors").json()["vendors"][0]["id"]==v["id"]


def test_guided_demo_rejects_stale_step_and_survives_restart(client):
    r=client.post("/api/demo/walkthrough/advance",json={"expected_step":0}).json()
    assert client.post("/api/demo/walkthrough/advance",json={"expected_step":0}).status_code==422
    db=client.app.state.pipeline.store.path
    with TestClient(create_app(db,status_get=lambda *a,**k:{"incidents":[]})) as again:
        assert again.get("/api/demo/walkthrough").json()["run_id"]==r["run_id"]
        assert again.post("/api/demo/walkthrough/advance",json={"expected_step":1}).json()["step"]==2
