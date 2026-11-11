"""Any agreement, end to end: read it, start a stored case with an uploaded log, price it, draft a claim."""
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

import api.main
from api.main import create_app
from conftest import scenario
from shark.claims.drafter import draft
from shark.engine import evaluate


def fake_extract(terms):
    def extract(source, service, plan, cache_dir, **kwargs):
        return {"mode": "live", "model": "claude-sonnet-5-5", "served_by": "claude-sonnet-5-5", "terms": terms.model_dump(mode="json"),
                "source_hash": "x", "extracted_at": "2026-10-30T00:00:00+00:00", "usage": {}}
    return extract


def own_log(client):
    """The sample's minutes with one quiet row changed: a log of the user's own, with the same 62 Downtime Minutes."""
    own = client.get("/api/logs/sample.csv").text.replace("\n2026-09-30T23:59Z,View Issue,4,0", "\n2026-09-30T23:59Z,View Issue,5,0")
    assert own != client.get("/api/logs/sample.csv").text
    return own


def form(read_id, sample, **overrides):
    return {"read_id": read_id, "customer_name": "Acme Robotics", "account_reference": "ACME-JIRA-1", "period": "2026-09",
            "as_of": "2026-10-08T12:00:00Z", "billing_cycle": "monthly", "fees": "9000", "receiving_invoice_fees": "9000",
            "prerequisites": {"production_instance": True, "account_fully_paid": True, "support_ticket_during_incident": True, "authorized_requester": True},
            "exclusions_reviewed": True, "csv": sample, **overrides}


def test_a_read_agreement_becomes_a_stored_priced_draftable_case(tmp_path, monkeypatch):
    terms = scenario("jira-sept-claim").terms
    monkeypatch.setattr(api.main, "extract", fake_extract(terms))
    db = str(tmp_path / "db.sqlite3")
    with TestClient(create_app(db)) as client:
        source = scenario("jira-sept-claim").source_text
        read = client.post("/api/agreements/read", json={"text": source, "service": "Jira", "plan": "Premium"}).json()
        assert read["read_id"] and read["case_prerequisites"] == ["production_instance", "account_fully_paid", "support_ticket_during_incident", "authorized_requester"]
        created = client.post("/api/cases", json=form(read["read_id"], own_log(client)))
        assert created.status_code == 200, created.text
        case_id = created.json()["id"]
        detail = client.get(f"/api/opportunities/{case_id}").json()
        d, s = detail["decision"], detail["scenario"]
        assert d["outcome"] == "eligible" and d["amount"] == "900.00" and d["downtime_minutes"] == "62"
        assert s["role"] == "user_case" and s["simulated"] is False and s["account"]["display_name"] == "Acme Robotics"
        assert {p["kind"] for p in s["provenance"]} == {"user"}  # pasted agreement text and user-entered records
        assert client.post(f"/api/opportunities/{case_id}/draft", json={"tone": "polite"}).status_code == 200
        assert case_id in {i["id"] for i in client.get("/api/opportunities").json()["items"]}
    with TestClient(create_app(db)) as client:  # survives a restart
        assert client.get(f"/api/opportunities/{case_id}").json()["claim"]["status"] == "draft"
        assert client.delete(f"/api/cases/{case_id}").status_code == 200
        assert client.get(f"/api/opportunities/{case_id}").status_code == 404


def test_case_form_must_answer_the_agreements_own_prerequisites(tmp_path, monkeypatch):
    monkeypatch.setattr(api.main, "extract", fake_extract(scenario("jira-sept-claim").terms))
    with TestClient(create_app(str(tmp_path / "db.sqlite3"))) as client:
        read = client.post("/api/agreements/read", json={"text": scenario("jira-sept-claim").source_text, "service": "Jira", "plan": "Premium"}).json()
        sample = client.get("/api/logs/sample.csv").text
        bad = client.post("/api/cases", json=form(read["read_id"], sample, prerequisites={"made_up": True}))
        assert bad.status_code == 422 and "production_instance" in bad.json()["detail"]
        incomplete = form(read["read_id"], sample)
        del incomplete["prerequisites"]["authorized_requester"]
        unauthorized = client.post("/api/cases", json=incomplete)
        assert unauthorized.status_code == 200
        decision = client.get(f"/api/opportunities/{unauthorized.json()['id']}").json()["decision"]
        assert decision["outcome"] == "needs_review" and decision["amount"] == "0.00"
        assert client.post(f"/api/opportunities/{unauthorized.json()['id']}/draft", json={"tone": "polite"}).status_code == 409
        unknown = client.post("/api/cases", json=form("nope", sample))
        assert unknown.status_code == 404


def missing(*args, **kwargs):
    raise FileNotFoundError("not cached")


def test_a_saved_run_without_its_source_cannot_start_a_case(tmp_path, monkeypatch):
    monkeypatch.setattr(api.main, "load_source", missing)
    with TestClient(create_app(str(tmp_path / "db.sqlite3"))) as client:
        body = client.post("/api/agreements/read", json={"catalog_id": "github"}).json()
    assert body["read_id"] is None and "re-read" in body["case_unavailable_reason"].lower() and body["verified_against"] is None


def test_in_a_fresh_clone_the_atlassian_run_is_checked_against_the_committed_excerpts(tmp_path, monkeypatch):
    monkeypatch.setattr(api.main, "load_source", missing)  # no private page cache, as in a fresh clone
    with TestClient(create_app(str(tmp_path / "db.sqlite3"))) as client:
        read = client.post("/api/agreements/read", json={"catalog_id": "atlassian"}).json()
        assert read["verified_now"] and read["verified_against"] == "excerpts" and read["readiness"]["can_price"]
        sample = client.get("/api/logs/sample.csv").text
        body = form(read["read_id"], sample, demo=True, prerequisites={p: True for p in read["case_prerequisites"]})
        case_id = client.post("/api/cases", json=body).json()["id"]
        detail = client.get(f"/api/opportunities/{case_id}").json()
    assert detail["decision"]["outcome"] == "eligible" and detail["decision"]["amount"] == "900.00"
    assert "excerpt" in next(p for p in detail["scenario"]["provenance"] if p["item"] == "Agreement terms")["detail"]


def test_unspecified_claim_fields_draft_with_a_note_but_named_unknown_fields_refuse():
    s = scenario("jira-sept-claim")
    d = evaluate(s)
    s.terms.claim_procedure.value.required_fields = ["account_reference", "other", "request_logs"]
    _, body = draft(s, d)
    assert "may ask for more fields" in body and "Attach the request logs" in body
    s.terms.claim_procedure.value.required_fields = ["affected_resources"]
    with pytest.raises(ValueError, match="additional claim fields"):
        draft(s, d)


def test_a_missing_log_file_sends_the_case_to_review(tmp_path):
    import json
    from shark.engine.metrics import apply_logs
    from shark.models import Scenario
    from shark.pipeline import ROOT
    raw = next(x for x in json.loads((ROOT / "data/fixtures/scenarios.json").read_text()) if x["id"] == "jira-sept-claim")
    s = Scenario.model_validate(raw)
    next(i for i in s.incidents if i.log).log.path = ".cache/uploads/missing.csv.gz"
    d = evaluate(apply_logs(s))
    assert d.outcome == "needs_review" and any("not been verified" in c.detail for c in d.checks)


def test_a_case_built_on_the_sample_log_is_labeled_simulated(tmp_path, monkeypatch):
    monkeypatch.setattr(api.main, "extract", fake_extract(scenario("jira-sept-claim").terms))
    with TestClient(create_app(str(tmp_path / "db.sqlite3"))) as client:
        read = client.post("/api/agreements/read", json={"text": scenario("jira-sept-claim").source_text, "service": "Jira", "plan": "Premium"}).json()
        sample = client.get("/api/logs/sample.csv").text
        case_id = client.post("/api/cases", json=form(read["read_id"], sample, demo=True)).json()["id"]
        s = client.get(f"/api/opportunities/{case_id}").json()["scenario"]
    assert s["simulated"] is True and all(i["simulated"] for i in s["incidents"])
    kinds = {p["item"]: p["kind"] for p in s["provenance"]}
    assert kinds["Agreement terms"] == "user" and kinds["Customer request logs"] == "simulated"
    assert kinds["Customer, invoice, and prerequisites"] == "simulated"


def test_a_log_the_agreement_cannot_measure_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(api.main, "extract", fake_extract(scenario("jira-sept-claim").terms))
    with TestClient(create_app(str(tmp_path / "db.sqlite3"))) as client:
        read = client.post("/api/agreements/read", json={"text": scenario("jira-sept-claim").source_text, "service": "Jira", "plan": "Premium"}).json()
        sample = client.get("/api/logs/sample.csv").text
        wrong_month = client.post("/api/cases", json=form(read["read_id"], sample, period="2026-07"))
        assert wrong_month.status_code == 422 and "no minutes in 2026-07" in wrong_month.json()["detail"]
        assert client.get("/api/opportunities").json()["items"] and not any(
            i["role"] == "user_case" for i in client.get("/api/opportunities").json()["items"])


def test_the_draft_banner_never_calls_a_pasted_agreement_simulated(tmp_path, monkeypatch):
    monkeypatch.setattr(api.main, "extract", fake_extract(scenario("jira-sept-claim").terms))
    with TestClient(create_app(str(tmp_path / "db.sqlite3"))) as client:
        read = client.post("/api/agreements/read", json={"text": scenario("jira-sept-claim").source_text, "service": "Jira", "plan": "Premium"}).json()
        case_id = client.post("/api/cases", json=form(read["read_id"], client.get("/api/logs/sample.csv").text, demo=True)).json()["id"]
        body = client.post(f"/api/opportunities/{case_id}/draft", json={"tone": "polite"}).json()["body"]
    banner = body.splitlines()[0]
    assert banner.startswith("DEMO CLAIM") and "customer request logs" in banner and "agreement terms" not in banner


def test_the_bundled_sample_log_is_labeled_simulated_however_it_is_uploaded(tmp_path, monkeypatch):
    monkeypatch.setattr(api.main, "extract", fake_extract(scenario("jira-sept-claim").terms))
    with TestClient(create_app(str(tmp_path / "db.sqlite3"))) as client:
        read = client.post("/api/agreements/read", json={"text": scenario("jira-sept-claim").source_text, "service": "Jira", "plan": "Premium"}).json()
        case_id = client.post("/api/cases", json=form(read["read_id"], client.get("/api/logs/sample.csv").text, demo=False)).json()["id"]
        assert client.get(f"/api/opportunities/{case_id}").json()["scenario"]["simulated"] is True
        rows = client.get("/api/export").text.splitlines()
    mine = next(r for r in rows if "Acme Robotics" in r)
    assert mine.endswith("Simulated: built on the bundled sample log")


def test_a_real_case_is_not_exported_as_simulated(tmp_path, monkeypatch):
    monkeypatch.setattr(api.main, "extract", fake_extract(scenario("jira-sept-claim").terms))
    with TestClient(create_app(str(tmp_path / "db.sqlite3"))) as client:
        read = client.post("/api/agreements/read", json={"text": scenario("jira-sept-claim").source_text, "service": "Jira", "plan": "Premium"}).json()
        created = client.post("/api/cases", json=form(read["read_id"], own_log(client)))
        assert created.status_code == 200, created.text
        rows = client.get("/api/export").text.splitlines()
    assert next(r for r in rows if "Acme Robotics" in r).endswith("Entered in this workspace")


def test_an_implausible_revisit_window_is_not_read():
    from shark.cases import dispute_rule
    assert dispute_rule("If you disagree, you have 99999999999 business days to request that we revisit the claim.") is None
    assert dispute_rule("If you disagree, you have 5 business days to request that we revisit the claim.").value.business_days == 5
