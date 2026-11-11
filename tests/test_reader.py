"""Reading a new agreement: what the engine could price, and a fetch that only reaches public sites."""
import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from conftest import scenario
from shark.engine.readiness import readiness
from shark.extract.citations import term_status
from shark.extract.fetch import public_url


def test_supported_agreement_is_ready_to_price(case):
    report = readiness(case.terms, term_status(case.terms, case.source_text))
    assert report["can_price"] is True
    assert all(item["status"] == "pass" for item in report["checks"])


@pytest.mark.parametrize("id,blocker", [("github", "Measurement period"), ("cloudflare", "Downtime metric")])
def test_unsupported_terms_name_the_blocker(id, blocker):
    s = scenario(id)
    report = readiness(s.terms, term_status(s.terms, s.source_text))
    assert report["can_price"] is False
    assert any(item["name"] == blocker and item["status"] == "review" for item in report["checks"])


def test_unverified_quote_blocks_pricing(case):
    case.terms.credit_tiers.quote = "Not in the source."
    report = readiness(case.terms, term_status(case.terms, case.source_text))
    assert report["can_price"] is False and any(item["name"] == "Quoted evidence" and item["status"] == "review" for item in report["checks"])


@pytest.mark.parametrize("url", ["http://example.com/sla", "https://127.0.0.1/sla", "https://localhost/sla", "https://10.0.0.5/x",
                                 "https://169.254.169.254/latest/meta-data", "file:///etc/passwd", "https://[::1]/",
                                 "https://224.0.0.1/x", "https://[64:ff9b::7f00:1]/x"])
def test_reader_refuses_non_public_urls(url):
    with pytest.raises(ValueError):
        public_url(url)


def test_read_endpoint_runs_extraction_and_reports_readiness(tmp_path, monkeypatch, case):
    import api.main
    calls = []
    def fake_extract(source, service, plan, cache_dir, **kwargs):
        calls.append((service, plan))
        return {"mode": "live", "model": "claude-sonnet-5-5", "served_by": "claude-sonnet-5-5", "terms": case.terms.model_dump(mode="json"),
                "source_hash": "x", "extracted_at": "2026-10-30T00:00:00+00:00", "usage": {}}
    monkeypatch.setattr(api.main, "extract", fake_extract)
    with TestClient(create_app(str(tmp_path / "db.sqlite3"))) as client:
        body = client.post("/api/agreements/read", json={"text": case.source_text, "service": "Jira", "plan": "Premium"}).json()
        assert calls == [("Jira", "Premium")]
        assert body["mode"] == "live" and body["readiness"]["can_price"] is True
        assert body["term_status"]["credit_tiers"] == "verified" and body["term_status"]["min_credit"] == "absent"
        assert client.post("/api/agreements/read", json={"service": "Jira", "plan": "Premium"}).status_code == 422
        assert client.post("/api/agreements/read", json={"url": "https://127.0.0.1/x", "service": "Jira", "plan": "Premium"}).status_code == 400


def test_read_endpoint_without_a_key_explains_itself(tmp_path, monkeypatch, case):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with TestClient(create_app(str(tmp_path / "db.sqlite3"))) as client:
        response = client.post("/api/agreements/read", json={"text": case.source_text, "service": "Jira", "plan": "Premium"})
    assert response.status_code == 409 and "ANTHROPIC_API_KEY" in response.json()["detail"]


def test_saved_atlassian_run_opens_without_a_key(tmp_path):
    with TestClient(create_app(str(tmp_path / "db.sqlite3"))) as client:
        listed = client.get("/api/agreements/saved").json()["agreements"]
        assert {a["id"] for a in listed} >= {"atlassian", "google-cloud-dns", "expo-eas"} and all(a["runs"] == 3 for a in listed)
        body = client.post("/api/agreements/read", json={"catalog_id": "atlassian"}).json()
    assert body["mode"] == "saved_run" and body["model"] == "claude-sonnet-5-5" and body["runs_saved"] == 3
    assert body["readiness"]["can_price"] is True and body["term_status"]["min_credit"] == "absent"
