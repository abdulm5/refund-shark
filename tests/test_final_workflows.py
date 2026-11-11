import copy
import io
import json
import zipfile

import pytest

from shark.bundle import build_bundle, verify_bundle, digest, encoded
from shark.challenges import challenge
from shark.pipeline import load_scenarios
from test_recovery import client, setup, confirm, evidence


def start(client, ticket=True):
    vendor, read = setup(client)
    evidence(client, vendor)
    if ticket:
        evidence(client, vendor, "ticket")
    confirm(client, "credit")
    r = client.post("/api/recovery/scan", json={"replay": True})
    assert r.status_code == 200, r.text
    item = r.json()["items"][0]
    return vendor, read, item


def review(client, item, **changes):
    body = {"expected_version": item["version"], "reviewer": "Test finance reviewer", "confirmed": True,
        "prerequisites": {p: True for p in item["requirements"]}, "exclusions_reviewed": True} | changes
    r = client.post(f"/api/recovery/workspace/proposals/{item['id']}/review", json=body)
    assert r.status_code == 200, r.text
    return next(i for i in r.json()["items"] if i["id"] == item["id"])


def accept(client, item):
    return client.post(f"/api/recovery/workspace/proposals/{item['id']}/accept",
        json={"expected_version": item["version"], "confirmed": True})


def test_scan_refreshes_on_ticket_arrival_without_changing_cases(client):
    _, _, item = start(client, ticket=False)
    item = review(client, item)
    assert item["status"] == "missing" and item["decision"]["amount"] == "0.00"
    store = client.app.state.pipeline.store
    assert not store.user_cases() and not store.claims()
    vendor = store.record("vendor", item["vendor_id"])
    evidence(client, vendor, "ticket")
    state = client.get("/api/recovery/scan").json()
    updated = state["items"][0]
    assert updated["status"] == "ready" and state["totals"]["supported"] == "900.00"
    assert updated["version"] != item["version"] and not store.user_cases()
    assert accept(client, item).status_code == 422
    r = accept(client, updated)
    assert r.status_code == 200, r.text
    saved = r.json()
    assert saved["claim"]["status"] == "draft" and saved["scan"]["items"][0]["status"] == "handled"
    assert len(store.user_cases()) == 1
    assert accept(client, updated).status_code == 422
    assert len(store.user_cases()) == 1


def test_scan_new_logs_invalidate_fact_review_but_not_saved_approval(client):
    vendor, _, item = start(client)
    item = review(client, item)
    saved = accept(client, item).json()
    case_id = saved["case_id"]
    d = client.get(f"/api/opportunities/{case_id}").json()
    client.post(f"/api/opportunities/{case_id}/actions", json={"action": "approve", "revision": d["revision"]})
    store = client.app.state.pipeline.store
    old = store.get_claim(case_id)
    evidence(client, vendor, label="Identical second export")
    item = client.get("/api/recovery/scan").json()["items"][0]
    assert item["status"] == "missing" and item["facts"] == {}
    assert store.get_claim(case_id) == old
    item = review(client, item)
    assert item["decision"]["amount"] == "900.00"
    updated = accept(client, item).json()
    assert updated["case_id"] == case_id and updated["claim"]["status"] == "draft"
    assert any(e["action"] == "invalidated" for e in updated["claim"]["events"])
    assert len(store.user_cases()) == 1


def test_scan_submitted_claim_is_handled_even_with_new_evidence(client):
    vendor, _, item = start(client)
    saved = accept(client, review(client, item)).json()
    case_id = saved["case_id"]
    d = client.get(f"/api/opportunities/{case_id}").json()
    for action in ["approve", "mark_sent"]:
        assert client.post(f"/api/opportunities/{case_id}/actions", json={"action": action, "revision": d["revision"]}).status_code == 200
    evidence(client, vendor, label="Later duplicate")
    assert client.get("/api/recovery/scan").json()["items"][0]["status"] == "handled"


def test_scan_ambiguous_invoice_and_unknown_facts_never_price(client):
    _, _, item = start(client)
    assert item["status"] == "missing"
    store = client.app.state.pipeline.store
    doc = store.record("document", item["invoice_id"])
    duplicate = copy.deepcopy(doc)
    duplicate["id"] += "-duplicate"
    store.save_record("document", duplicate)
    state = client.get("/api/recovery/scan").json()
    assert state["items"][0]["decision"] is None and state["totals"]["supported"] == "0.00"
    assert "Multiple confirmed invoices" in state["items"][0]["blockers"][0]["detail"]


def test_scan_conflicting_logs_and_stale_review_are_refused(client):
    vendor, _, item = start(client)
    evidence(client, vendor, label="Conflict", csv="minute,experience,requests,errors\n2026-09-03T15:00:00Z,View Issue,900,90\n")
    r = client.post(f"/api/recovery/workspace/proposals/{item['id']}/review", json={"expected_version": item["version"],
        "reviewer": "Test", "confirmed": True})
    assert r.status_code == 422
    state = client.get("/api/recovery/scan").json()
    assert state["totals"]["supported"] == "0.00"
    assert any("Conflicting counts" in b["detail"] for b in state["items"][0]["blockers"])


def test_scan_duplicate_vendor_profiles_do_not_double_total(client):
    vendor, _, item = start(client)
    review(client, item)
    store = client.app.state.pipeline.store
    second = store.record("vendor", vendor["id"]) | {"id": "vendor-duplicate"}
    store.save_record("vendor", second)
    state = client.get("/api/recovery/scan").json()
    assert len(state["items"]) == 1 and state["totals"]["supported"] == "0.00"
    assert state["items"][0]["blockers"][0]["name"] == "Duplicate vendor setups"


def test_scan_replay_never_backdates_real_profiles(client):
    vendor, _, _ = start(client)
    store = client.app.state.pipeline.store
    v = store.record("vendor", vendor["id"]) | {"simulated": False}
    store.save_record("vendor", v)
    assert client.post("/api/recovery/scan", json={"replay": True}).status_code == 422
    assert client.post("/api/recovery/scan", json={"replay": False}).status_code == 200


def test_scan_draft_validation_failure_cannot_be_accepted(client, monkeypatch):
    _, _, item = start(client)
    def refuse_draft(*args):
        raise ValueError("A required claim field cannot be rendered.")
    monkeypatch.setattr("shark.scan.draft", refuse_draft)
    item = review(client, item)
    assert item["status"] == "missing" and item["draft"] is None
    assert client.get("/api/recovery/scan").json()["totals"]["supported"] == "0.00"
    assert accept(client, item).status_code == 422


@pytest.mark.parametrize("action,outcome,amount", [("missing_ticket", "needs_review", "0.00"), ("late_ticket", "not_eligible", "0.00"),
    ("maintenance", "not_eligible", "0.00"), ("deadline", "not_eligible", "0.00"), ("duplicate", "eligible", "900.00"),
    ("missing_logs", "needs_review", "0.00"), ("citation", "needs_review", "0.00")])
def test_challenges_use_engine_and_never_mutate_saved_case(client, action, outcome, amount):
    pipeline = client.app.state.pipeline
    s = pipeline.scenarios["jira-sept-claim"]
    original = s.model_dump_json()
    claim = pipeline.create_claim(s.id)
    r = client.post(f"/api/recovery/workspace/cases/{s.id}/challenges", json={"action": action})
    assert r.status_code == 200, r.text
    result = r.json()
    assert result["before"]["amount"] == "900.00" and result["after"]["outcome"] == outcome and result["after"]["amount"] == amount
    assert s.model_dump_json() == original and pipeline.store.get_claim(s.id) == claim
    if action == "duplicate":
        assert result["after"]["downtime_minutes"] == result["before"]["downtime_minutes"]


def test_bundle_replays_offline_and_contains_only_selected_case(client):
    _, _, item = start(client)
    saved = accept(client, review(client, item)).json()
    raw = client.get(f"/api/recovery/workspace/cases/{saved['case_id']}/bundle").content
    result = verify_bundle(raw)
    assert result["verified"] and result["amount"] == "900.00"
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        assert len([n for n in z.namelist() if n.endswith(".pdf")]) == 2
        assert not any(".env" in n for n in z.namelist())
        assert b"SHARK_DATADOG" not in z.read("manifest.json")
        assert not json.loads(z.read("scenario.json"))["incidents"][0]["log"]["path"].startswith("/")


@pytest.mark.parametrize("change", ["tamper", "decision", "traversal", "engine", "missing", "duplicate"])
def test_bundle_refuses_tampering_and_unsafe_archives(client, change):
    pipeline = client.app.state.pipeline
    raw = build_bundle(pipeline.store, pipeline.scenarios["jira-sept-claim"])
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        files = {n: z.read(n) for n in z.namelist()}
    manifest = json.loads(files["manifest.json"])
    if change == "tamper": files["agreement.txt"] += b"changed"
    if change == "decision":
        d = json.loads(files["decision.json"]); d["amount"] = "999.00"
        files["decision.json"] = encoded(d)
        manifest["files"]["decision.json"] = {"sha256": digest(files["decision.json"]), "bytes": len(files["decision.json"])}
    if change == "traversal": files["../escape"] = b"no"
    if change == "engine": manifest["engine_files"] = {}
    if change == "missing": del files["terms.json"]
    files["manifest.json"] = encoded(manifest)
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        for n, value in files.items(): z.writestr(n, value)
        if change == "duplicate":
            with pytest.warns(UserWarning): z.writestr("terms.json", files["terms.json"])
    with pytest.raises(ValueError): verify_bundle(target.getvalue())


def test_portfolio_replay_evidence_arrival_and_reset_are_isolated(client):
    store = client.app.state.pipeline.store
    before = (store.records("vendor"), store.claims(), store.user_cases())
    r = client.post("/api/recovery/portfolio/reset")
    assert r.status_code == 200, r.text
    state = r.json()
    assert state["totals"] == {"ready": 1, "missing": 1, "ineligible": 1, "handled": 0, "supported": "900.00"}
    arrived = client.post("/api/recovery/portfolio/arrive").json()
    assert arrived["totals"]["supported"] == "1380.00" and arrived["totals"]["ineligible"] == 1
    assert client.post("/api/recovery/portfolio/arrive").json()["totals"] == arrived["totals"]
    item = next(i for i in arrived["items"] if i["status"] == "ready")
    result = client.post(f"/api/recovery/portfolio/proposals/{item['id']}/accept", json={"expected_version": item["version"], "confirmed": True})
    assert result.status_code == 200, result.text
    raw = client.get(f"/api/recovery/portfolio/cases/{result.json()['case_id']}/bundle").content
    assert verify_bundle(raw)["verified"]
    assert client.post("/api/recovery/portfolio/reset").json()["totals"]["supported"] == "900.00"
    assert (store.records("vendor"), store.claims(), store.user_cases()) == before


def test_demo_entry_resumes_progress_instead_of_resetting(client):
    assert not client.get("/api/recovery/portfolio").json()["started"]
    first = client.post("/api/recovery/portfolio/start")
    assert first.status_code == 200 and first.json()["totals"]["supported"] == "900.00"
    store = client.app.state.pipeline.store
    run = store.record("portfolio_replay", "current")["run_id"]
    client.post("/api/recovery/portfolio/arrive")
    resumed = client.post("/api/recovery/portfolio/start").json()
    assert resumed["evidence_arrived"] and resumed["totals"]["supported"] == "1380.00"
    assert store.record("portfolio_replay", "current")["run_id"] == run
    assert not store.user_cases() and not store.claims()


def test_reference_scoring_requires_complete_human_attestation(tmp_path, monkeypatch):
    from eval import review_labels
    s = next(s for s in load_scenarios() if s.id == "jira-sept-claim")
    manifest = tmp_path / "manifest.json"
    paths = []
    for n in range(1, 4):
        path = tmp_path / f"run{n}.json"
        path.write_text(json.dumps({"run": n, "source_hash": s.source_hash, "terms": s.terms.model_dump(mode="json")}))
        paths.append(path.name)
    manifest.write_text(json.dumps({"documents": [{"id": "atlassian", "extraction_paths": paths, "scenario_ids": [s.id]}]}))
    monkeypatch.setattr(review_labels, "GOLD_MANIFEST", manifest)
    monkeypatch.setattr(review_labels, "load_source", lambda *_: s.source_text)
    body = {"document": "atlassian", "source_hash": s.source_hash, "reviewer": "Synthetic test reviewer", "reviewed_at": "2026-11-10",
        "confirmed_human_review": False, "fields_reviewed": {k: True for k in type(s.terms).model_fields}, "terms": s.terms.model_dump(mode="json")}
    with pytest.raises(ValueError, match="person must review"):
        review_labels.score(body)
    body["ai_review"] = {"reviewer": "Synthetic AI reviewer", "reviewed_at": "2026-11-10", "fields_reviewed": body["fields_reviewed"].copy()}
    ai_report = review_labels.audit(body)
    assert ai_report["mode"] == "post_hoc_ai_assisted_reference_audit"
    assert ai_report["confirmed_human_review"] is False and body["confirmed_human_review"] is False
    assert ai_report["worst_material_field_agreement"] == 1
    assert "field_accuracy" not in ai_report["runs"][0]
    with pytest.raises(ValueError, match="person must review"):
        review_labels.score(body)
    body["ai_review"]["fields_reviewed"]["credit_tiers"] = False
    with pytest.raises(ValueError, match="all 16 checked fields"):
        review_labels.audit(body)
    body["ai_review"]["fields_reviewed"]["credit_tiers"] = True
    body["confirmed_human_review"] = True
    with pytest.raises(ValueError, match="leave human review unconfirmed"):
        review_labels.audit(body)
    body["fields_reviewed"]["credit_tiers"] = False
    with pytest.raises(ValueError, match="Every reference field"):
        review_labels.score(body)
    body["fields_reviewed"]["credit_tiers"] = True
    report = review_labels.score(body)
    assert report["worst_field_agreement"] == 1 and report["false_positive_decisions"] == 0
    assert report["mode"] == "post_hoc_human_reference_review"
    body["source_hash"] = "wrong"
    with pytest.raises(ValueError, match="frozen source"):
        review_labels.score(body)
