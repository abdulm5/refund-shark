"""The watcher: notice an outage once, alert only who could be owed, and follow up when it ends."""
from datetime import datetime, timezone

import httpx

from shark.incidents.watcher import Watcher
from shark.pipeline import load_scenarios
from shark.storage import Store

JIRA = "jira-software.status.atlassian.com"
NOW = datetime(2026, 10, 30, 15, 0, tzinfo=timezone.utc)
US_EAST = {"id": "inc1", "name": "Jira errors in US East", "status": "investigating", "impact": "major", "started_at": "2026-10-30T14:30:00Z",
           "shortlink": "https://stspg.io/inc1", "components": [{"name": "Viewing content"}], "incident_updates": []}


class Feeds:
    def __init__(self):
        self.open, self.down = {}, set()

    def get(self, url, timeout):
        host = url.split("/")[2]
        if host in self.down:
            raise httpx.ConnectError("offline")
        return {"incidents": list(self.open.get(host, []))}


def watcher(tmp_path, feeds):
    return Watcher(Store(str(tmp_path / "w.sqlite3")), load_scenarios(), get=feeds.get)


def test_new_outage_alerts_matched_customers_once(tmp_path):
    feeds = Feeds(); feeds.open[JIRA] = [US_EAST]
    w = watcher(tmp_path, feeds)
    report = w.poll(NOW)
    alerts = w.store.alerts()
    assert report["new_alerts"] == 2 and {a["customer"] for a in alerts} == {"Northstar Analytics (demo)", "Harbor Logistics (demo)"}
    assert all(a["kind"] == "ticket_now" and "stspg.io/inc1" in a["ticket"] and not a["simulated"] for a in alerts)
    assert w.poll(NOW)["new_alerts"] == 0 and len(w.store.alerts()) == 2
    assert any("Jira" in e["title"] and "detected" in e["title"] for e in w.store.events())


def test_unreachable_feed_never_resolves_an_incident(tmp_path):
    feeds = Feeds(); feeds.open[JIRA] = [US_EAST]
    w = watcher(tmp_path, feeds)
    w.poll(NOW)
    feeds.open[JIRA] = []; feeds.down.add(JIRA)
    report = w.poll(NOW)
    assert not any(f["ok"] for f in report["feeds"] if f["host"] == JIRA)
    assert [a["kind"] for a in w.store.alerts()] == ["ticket_now", "ticket_now"]


def test_resolution_tells_each_alerted_customer_the_filing_deadline(tmp_path):
    feeds = Feeds(); feeds.open[JIRA] = [US_EAST]
    w = watcher(tmp_path, feeds)
    w.poll(NOW)
    feeds.open[JIRA] = []
    report = w.poll(NOW.replace(hour=16))
    follow_ups = [a for a in w.store.alerts() if a["kind"] == "file_by"]
    assert report["resolved"] == 1 and {a["customer"] for a in follow_ups} == {"Northstar Analytics (demo)", "Harbor Logistics (demo)"}
    assert all("Nov 15, 2026 09:59 UTC" in a["detail"] for a in follow_ups)  # earliest reading of the October window
    assert w.poll(NOW.replace(hour=17))["new_alerts"] == 0


def test_drills_are_labeled_simulated_end_to_end(tmp_path):
    feeds = Feeds()
    w = watcher(tmp_path, feeds)
    w.start_drill(NOW)
    opened = w.store.alerts()
    assert opened and all(a["simulated"] for a in opened) and all("Simulated" in a["incident_name"] for a in opened)
    w.end_drill(NOW.replace(hour=16))
    assert any(a["kind"] == "file_by" and a["simulated"] for a in w.store.alerts())


def test_alerts_can_be_marked_read(tmp_path):
    feeds = Feeds(); feeds.open[JIRA] = [US_EAST]
    w = watcher(tmp_path, feeds)
    w.poll(NOW)
    first = w.store.alerts()[0]
    w.store.mark_alert_read(first["id"])
    assert w.store.unread_alerts() == 1


def test_api_runs_drills_and_lists_alerts(tmp_path):
    from fastapi.testclient import TestClient
    from api.main import create_app
    with TestClient(create_app(str(tmp_path / "api.sqlite3"), status_get=Feeds().get, watch_interval=0)) as client:
        assert client.get("/api/alerts").json()["unread"] == 0
        assert client.post("/api/watch/drill", json={"action": "start"}).json()["new_alerts"] == 2
        body = client.get("/api/alerts").json()
        assert body["unread"] == 2 and body["watch"]["drill_active"] and all(a["simulated"] for a in body["alerts"])
        client.post(f"/api/alerts/{body['alerts'][0]['id']}/read")
        assert client.get("/api/alerts").json()["unread"] == 1
        client.post("/api/watch/drill", json={"action": "end"})
        assert {a["kind"] for a in client.get("/api/alerts").json()["alerts"]} == {"ticket_now", "file_by"}
        assert client.post("/api/watch/poll").json()["feeds"]


def test_background_watcher_polls_on_startup(tmp_path):
    import time
    from fastapi.testclient import TestClient
    from api.main import create_app
    feeds = Feeds(); feeds.open[JIRA] = [US_EAST]
    with TestClient(create_app(str(tmp_path / "api.sqlite3"), status_get=feeds.get, watch_interval=3600)) as client:
        for _ in range(50):
            if client.get("/api/alerts").json()["unread"]:
                break
            time.sleep(0.05)
        body = client.get("/api/alerts").json()
    assert body["unread"] == 2 and body["watch"]["interval_seconds"] == 3600 and body["watch"]["last_poll"]


def test_watch_command_polls_once(tmp_path, monkeypatch, capsys):
    import json
    import shark.incidents.live as live
    from shark.cli import main
    feeds = Feeds(); feeds.open[JIRA] = [US_EAST]
    monkeypatch.setattr(live, "http_get", feeds.get)
    monkeypatch.setenv("SHARK_DB", str(tmp_path / "cli.sqlite3"))
    main(["watch", "--once"])
    assert json.loads(capsys.readouterr().out)["new_alerts"] == 2


def test_alerts_api_explains_who_was_not_alerted(tmp_path):
    from fastapi.testclient import TestClient
    from api.main import create_app
    with TestClient(create_app(str(tmp_path / "api.sqlite3"), status_get=Feeds().get, watch_interval=0)) as client:
        client.post("/api/watch/drill", json={"action": "start"})
        [incident] = client.get("/api/alerts").json()["incidents"]
    assert incident["open"] and incident["simulated"]
    skipped = {m["customer"]: m["status"] for m in incident["matches"] if m["status"] not in {"affected", "possibly_affected"}}
    assert skipped == {"Cobalt Studio (demo)": "other_region", "Juniper Health (demo)": "other_region", "Pinecrest Labs (demo)": "no_sla"}


def test_suite_never_polls_the_network_by_default(tmp_path):
    # A developer's .env may set SHARK_WATCH_INTERVAL; the suite must stay offline and deterministic anyway.
    from fastapi.testclient import TestClient
    from api.main import create_app
    with TestClient(create_app(str(tmp_path / "api.sqlite3"))) as client:
        assert client.get("/api/alerts").json()["watch"]["interval_seconds"] == 0


def test_quarterly_agreements_do_not_get_a_monthly_deadline(tmp_path):
    w = watcher(tmp_path, Feeds())
    assert w.deadline_text("GitHub", "Enterprise Cloud", {"started_at": "2026-10-30T14:30:00Z"}, NOW) == "the deadline in the agreement"
