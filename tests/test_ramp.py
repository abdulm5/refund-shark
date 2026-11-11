"""Ramp spend import: only fields Ramp's public API reference documents, cited line by line; plans are never guessed."""
import json

import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from shark.incidents.live import ticket_text
from shark.incidents.matching import load_directory, match_customers
from shark.pipeline import ROOT, load_scenarios
from shark.spend.ramp import CITED, EXCERPT, RECORD, SAMPLE, UNKNOWN_PLAN, FixtureRamp, HttpRamp, excerpt_blocks, import_business, read

SEPT_3 = next(i for i in json.loads((ROOT / "docs/research/real-incidents.json").read_text())["incidents"] if i["id"] == "ws0393rxcgyl")


def test_every_field_read_is_quoted_from_the_public_reference():
    record = json.loads(RECORD.read_text())
    from shark.extract.citations import source_hash
    assert source_hash(EXCERPT.read_text()) == record["excerpt_sha256"]
    assert all(d["url"].startswith("https://docs.ramp.com/") for d in record["documents"])
    blocks = excerpt_blocks()
    for (block, field), line in CITED.items():
        assert line in blocks[block], (block, field, line)


def test_reading_a_field_without_a_citation_refuses():
    with pytest.raises(KeyError):
        read({"memo": "Jira Premium renewal"}, "List transactions", "memo")


def test_sample_business_becomes_watched_subscriptions_with_the_plan_unknown():
    customer, report = import_business(FixtureRamp(SAMPLE), simulated=True)
    assert customer.display_name == "Lumen Freight (demo)" and customer.simulated and customer.source == "ramp"
    subs = {s.vendor: s for s in customer.subscriptions}
    assert set(subs) == {"Atlassian", "GitHub"}
    atlassian = subs["Atlassian"]
    # The memo says "Jira Premium renewal" and the descriptor names the plan; neither is read.
    assert atlassian.plan == UNKNOWN_PLAN and atlassian.product is None and atlassian.billing_cycle == "annual"
    assert "2 charges" in atlassian.evidence and "declined" not in atlassian.evidence.lower()
    assert subs["GitHub"].billing_cycle == "monthly"
    assert "Figma" in report["not_watched"]


def test_unknown_plan_is_possibly_affected_but_never_affected_or_dropped():
    customer, _ = import_business(FixtureRamp(SAMPLE), simulated=True)
    directory = load_directory() + [customer]
    match = next(m for m in match_customers(SEPT_3, "Atlassian", "Jira", directory, load_scenarios()) if m["customer"] == "Lumen Freight (demo)")
    assert match["status"] == "possibly_affected" and "plan" in match["reason"].lower()
    assert match["reason"].count("request logs") == 1
    marketplace = {"id": "z", "name": "Marketplace apps failing", "incident_updates": [], "components": [{"name": "Marketplace"}]}
    outside = next(m for m in match_customers(marketplace, "Atlassian", "Jira", directory, load_scenarios()) if m["customer"] == "Lumen Freight (demo)")
    assert outside["status"] == "outside_covered"


def test_a_ticket_for_an_unknown_plan_does_not_name_one():
    text = ticket_text("Jira", UNKNOWN_PLAN, None, {"id": "x", "name": "Jira errors"})
    assert UNKNOWN_PLAN not in text and "Subject: Jira service disruption" in text


def test_http_client_sends_the_token_only_to_the_api_host_and_follows_next():
    calls = []
    pages = {"https://api.ramp.com/developer/v1/vendors": {"data": [{"id": "v1", "name": "Atlassian"}], "page": {"next": "https://api.ramp.com/developer/v1/vendors&start=v1"}},
             "https://api.ramp.com/developer/v1/vendors&start=v1": {"data": [{"id": "v2", "name": "GitHub"}], "page": {"next": None}}}

    def get(url, headers, timeout):
        calls.append((url, headers["Authorization"]))
        return pages[url]
    ramp = HttpRamp("ramp_business_tok_test", get=get)
    assert [v["id"] for v in ramp.vendors()] == ["v1", "v2"]
    assert all(auth == "Bearer ramp_business_tok_test" for _, auth in calls)
    pages["https://api.ramp.com/developer/v1/vendors"]["page"]["next"] = "https://evil.example/steal"
    with pytest.raises(ValueError, match="host"):
        list(HttpRamp("ramp_business_tok_test", get=get).vendors())


def test_importing_during_an_open_incident_alerts_the_new_customer(tmp_path):
    db = str(tmp_path / "db.sqlite3")
    with TestClient(create_app(db, status_get=lambda url, timeout: {"incidents": []})) as client:
        client.post("/api/watch/drill", json={"action": "start"})
        imported = client.post("/api/spend/ramp/import", json={"source": "sample"})
        assert imported.status_code == 200, imported.text
        assert imported.json()["new_alerts"] == 1
        alerts = client.get("/api/alerts").json()["alerts"]
        lumen = [a for a in alerts if a["customer"] == "Lumen Freight (demo)"]
        assert len(lumen) == 1 and lumen[0]["kind"] == "ticket_now" and lumen[0]["match_status"] == "possibly_affected"
        assert client.post("/api/spend/ramp/import", json={"source": "sample"}).json()["new_alerts"] == 0  # idempotent
        assert client.post("/api/spend/ramp/import", json={"source": "api"}).status_code == 409  # no RAMP_ACCESS_TOKEN
    with TestClient(create_app(db, status_get=lambda url, timeout: {"incidents": []})) as client:
        customers = client.get("/api/spend/customers").json()["customers"]
        assert [c["display_name"] for c in customers] == ["Lumen Freight (demo)"]
        assert client.delete(f"/api/spend/customers/{customers[0]['customer_id']}").status_code == 200
        assert client.get("/api/spend/customers").json()["customers"] == []


def test_verify_sources_checks_the_ramp_excerpt_against_the_cached_docs(tmp_path, monkeypatch):
    from shark.cli import verify_sources
    import shark.spend.ramp as ramp
    report = {s["id"]: s for s in verify_sources(cache_root=tmp_path)["sources"]}
    entry = report["ramp-developer-api"]
    assert entry["excerpt_intact"] is True and entry["snapshot"] in {"verified", "not_cached"}
    changed = tmp_path / "excerpt.txt"
    changed.write_text(EXCERPT.read_text().replace("merchant_name?: string | null", "merchant_name: string"))
    monkeypatch.setattr(ramp, "EXCERPT", changed)
    assert {s["id"]: s for s in verify_sources(cache_root=tmp_path)["sources"]}["ramp-developer-api"]["excerpt_intact"] is False


def test_a_merchant_with_an_undated_charge_is_still_reported(tmp_path):
    saved = json.loads(SAMPLE.read_text())
    saved["transactions"]["data"].append({"merchant_name": "Notion Labs", "state": "CLEARED"})
    path = tmp_path / "business.json"
    path.write_text(json.dumps(saved))
    _, report = import_business(FixtureRamp(path), simulated=True)
    assert "Notion Labs" in report["not_watched"]


def test_the_response_envelope_is_cited_too():
    blocks = excerpt_blocks()
    for block in ("List vendors · /developer/v1/vendors", "List transactions · /developer/v1/transactions"):
        assert CITED[(block, "data")] in blocks[block] and CITED[(block, "page")] in blocks[block]
