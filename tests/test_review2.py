"""Regression tests for the second independent review."""
import json
from datetime import datetime, timezone

from conftest import scenario
from shark.engine import evaluate
from shark.incidents.live import live_status
from shark.incidents.matching import load_directory, match_customers
from shark.incidents.watcher import Watcher
from shark.pipeline import load_scenarios
from shark.storage import Store

NOW = datetime(2026, 10, 30, 15, 0, tzinfo=timezone.utc)
GITHUB_INCIDENT = {"id": "gh1", "name": "Actions degraded", "status": "investigating", "started_at": "2026-10-30T14:00:00Z", "components": [], "incident_updates": []}


def feed(host_incidents):
    def get(url, timeout):
        return {"incidents": list(host_incidents.get(url.split("/")[2], []))}
    return get


def test_authored_agreements_never_produce_tickets_or_alerts(tmp_path):
    get = feed({"www.githubstatus.com": [GITHUB_INCIDENT]})
    github = next(f for f in live_status(load_scenarios(), get)["feeds"] if f["vendor"] == "GitHub")
    assert github["incidents"][0]["action"] is None and "authored" in github["incidents"][0]["action_note"].lower()
    w = Watcher(Store(str(tmp_path / "w.sqlite3")), load_scenarios(), get=get)
    assert w.poll(NOW)["new_alerts"] == 0 and w.store.alerts() == []


def test_clean_month_of_logs_is_refused_not_reviewed():
    s = scenario("jira-sept-claim")
    record = next(i for i in s.incidents if i.evidence_kind == "customer_metrics")
    record.intervals, record.experiences = [], []  # what a log with no Downtime Minutes yields
    d = evaluate(s)
    assert d.outcome == "not_eligible" and "meets the agreement" in d.reason


def test_platform_outage_on_an_unlisted_component_is_possibly_affected():
    login = {"id": "x", "name": "Users unable to log in to Jira", "components": [{"name": "Authentication and User Management"}],
             "incident_updates": [{"body": "Customers in US East cannot authenticate."}]}
    statuses = {m["customer"]: m["status"] for m in match_customers(login, "Atlassian", "Jira", load_directory(), load_scenarios())}
    assert statuses["Northstar Analytics (demo)"] == "possibly_affected" and statuses["Cobalt Studio (demo)"] == "other_region"


def test_a_malformed_status_response_resolves_nothing(tmp_path):
    incident = {"id": "inc1", "name": "Jira errors in US East", "started_at": "2026-10-30T14:30:00Z", "components": [{"name": "Viewing content"}], "incident_updates": []}
    responses = {"ok": {"incidents": [incident]}}
    def get(url, timeout):
        return responses["ok"] if "atlassian" in url else {"incidents": []}
    w = Watcher(Store(str(tmp_path / "w.sqlite3")), load_scenarios(), get=get)
    w.poll(NOW)
    for bad in ({"error": "maintenance"}, {"incidents": None}, {"incidents": [{"name": "no id"}]}):
        responses["ok"] = bad
        report = w.poll(NOW)
        assert report["resolved"] == 0 and not next(f for f in report["feeds"] if "atlassian" in f["host"])["ok"]
    assert {a["kind"] for a in w.store.alerts()} == {"ticket_now"}


def test_consistency_counts_machine_checkable_exclusions():
    from eval.run_eval import material
    terms = scenario("jira-sept-claim").terms
    flipped = terms.model_copy(deep=True)
    flipped.exclusions.value[0].machine_checkable = not flipped.exclusions.value[0].machine_checkable
    assert material("exclusions", terms.exclusions.value) != material("exclusions", flipped.exclusions.value)


def test_fresh_clone_eval_still_reports_saved_runs(tmp_path):
    import shutil, subprocess, sys
    from shark.pipeline import ROOT
    shutil.copytree(ROOT / "data", tmp_path / "data")  # no .cache: raw pages are private
    out = tmp_path / "report.json"
    subprocess.run([sys.executable, "-m", "eval.run_eval", "--manifest", str(tmp_path / "data/gold/manifest.json"), "--output", str(out)],
                   cwd=ROOT, check=True, capture_output=True)
    live = json.loads(out.read_text())["live_extraction"]
    assert live["status"] == "runs_saved" and "not cached" in live["label"]


def test_tests_never_load_the_developer_env_file(monkeypatch):
    import os
    import dotenv
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    dotenv.load_dotenv()
    from shark.cli import main  # noqa: F401  importing the CLI or API must not load .env either
    import api.main  # noqa: F401
    assert "ANTHROPIC_API_KEY" not in os.environ
