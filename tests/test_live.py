"""Live status: a real-time nudge, because Atlassian's credit needs a ticket opened during the incident."""
import httpx
import pytest

from shark.incidents.live import live_status
from shark.incidents.statuspage import is_statuspage_host
from shark.pipeline import load_scenarios

OPEN = {"incidents": [{"id": "abc123", "name": "Jira degraded in EU", "impact": "major", "status": "investigating",
                       "started_at": "2026-10-30T14:00:00.000Z", "shortlink": "https://stspg.io/abc123",
                       "components": [{"name": "Viewing content"}]}]}


def getter(responses):
    def get(url, timeout):
        host = url.split("/")[2]
        result = responses.get(host, {"incidents": []})
        if isinstance(result, Exception):
            raise result
        return result
    return get


def test_open_incident_prompts_a_ticket_with_the_agreement_quote():
    status = live_status(load_scenarios(), getter({"jira-software.status.atlassian.com": OPEN}))
    jira = next(f for f in status["feeds"] if f["product"] == "Jira")
    assert jira["ok"] and [i["id"] for i in jira["incidents"]] == ["abc123"]
    action = jira["incidents"][0]["action"]
    assert "during the incident" in action["requirement_quote"]
    # "in EU": only the EU customer with an SLA gets a ticket; the rest are listed with the reason they are not.
    assert {t["customer"] for t in action["tickets"]} == {"Cobalt Studio (demo)"}
    reasons = {m["customer"]: m["status"] for m in jira["incidents"][0]["matches"]}
    assert reasons["Northstar Analytics (demo)"] == "other_region" and reasons["Pinecrest Labs (demo)"] == "no_sla"
    ticket = action["tickets"][0]["text"]
    assert "abc123" in ticket and "https://stspg.io/abc123" in ticket and "2026-10-30 14:00 UTC" in ticket
    assert status["open_incidents"] == 1


def test_unreachable_feed_is_unknown_not_healthy():
    status = live_status(load_scenarios(), getter({"www.githubstatus.com": httpx.ConnectError("offline")}))
    github = next(f for f in status["feeds"] if f["vendor"] == "GitHub")
    assert github["ok"] is False and github["incidents"] == [] and "unavailable" in github["message"]


@pytest.mark.parametrize("host,ok", [("jira-software.status.atlassian.com", True), ("www.githubstatus.com", True), ("evilgithubstatus.com", False),
                                     ("status.atlassian.com.evil.io", False), ("www.cloudflarestatus.com", True), ("notcloudflarestatus.com", False)])
def test_statuspage_host_allowlist(host, ok):
    assert is_statuspage_host(host) is ok


def test_live_endpoint_caches_for_a_minute(tmp_path):
    from fastapi.testclient import TestClient
    from api.main import create_app
    calls = []
    def get(url, timeout):
        calls.append(url)
        return OPEN if "atlassian" in url else {"incidents": []}
    with TestClient(create_app(str(tmp_path / "db.sqlite3"), status_get=get)) as client:
        first = client.get("/api/status/live").json()
        second = client.get("/api/status/live").json()
    assert first["open_incidents"] == 1 and first == second and len(calls) == 3


def test_preview_adds_a_labeled_simulated_incident():
    status = live_status(load_scenarios(), getter({}), preview=True)
    jira = next(f for f in status["feeds"] if f["product"] == "Jira")
    [incident] = jira["incidents"]
    assert incident["simulated"] is True and incident["name"].startswith("Simulated preview")
    assert {t["customer"] for t in incident["action"]["tickets"]} == {"Northstar Analytics (demo)", "Harbor Logistics (demo)"}
    assert status["preview"] is True
    assert all(not f["incidents"] for f in status["feeds"] if f["product"] != "Jira")
