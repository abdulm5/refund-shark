import json

import pytest

from shark.cli import main


def run(capsys, *argv):
    main(list(argv))
    return json.loads(capsys.readouterr().out)


def test_run_selects_by_vendor_product_plan_and_period(capsys):
    out = run(capsys, "run", "--vendor", "atlassian", "--product", "jira", "--plan", "premium", "--period", "2026-10")
    assert [r["scenario"] for r in out["results"]] == ["jira-oct-degraded"]
    assert out["results"][0]["decision"]["outcome"] == "needs_review"


def test_run_replays_the_labeled_clock_unless_given_another(capsys):
    replay = run(capsys, "run", "--scenario", "jira-sept-claim")["results"][0]
    assert replay["as_of"].startswith("2026-10-08") and replay["decision"]["outcome"] == "eligible"
    later = run(capsys, "run", "--scenario", "jira-sept-claim", "--as-of", "2026-10-29T12:00:00Z")["results"][0]
    assert later["decision"]["outcome"] == "not_eligible"


def test_run_explains_an_unknown_selection(capsys):
    with pytest.raises(SystemExit):
        main(["run", "--vendor", "atlassian", "--period", "2026-08"])
    assert "No bundled scenario" in capsys.readouterr().err


def test_verify_sources_checks_committed_excerpts(capsys):
    out = run(capsys, "verify-sources")
    assert out["sources"] and all(s["excerpt_intact"] for s in out["sources"])
    assert {s["snapshot"] for s in out["sources"]} <= {"verified", "not_cached"}


def test_verify_sources_reports_a_changed_fresh_fetch(tmp_path):
    from shark.cli import verify_sources
    fresh = tmp_path / "atlassian" / "20261101T000000Z"
    fresh.mkdir(parents=True)
    (fresh / "source.html").write_text("<html><body><article><p>" + "A different service level agreement. " * 20 + "</p></article></body></html>")
    result = {s["id"]: s for s in verify_sources(cache_root=tmp_path)["sources"]}
    latest = result["atlassian-sla"]["latest_fetch"]
    assert latest["retrieved"] == "20261101T000000Z"
    assert latest["raw_matches_recorded"] is False and latest["missing_excerpt_lines"] > 0
    assert "latest_fetch" not in result["atlassian-compensation"]


def test_logs_command_measures_any_request_log(capsys, tmp_path):
    log = tmp_path / "requests.csv"
    log.write_text("minute,experience,requests,errors\n2026-09-03T15:00Z,View Issue,100,10\n2026-09-03T15:01Z,View Issue,100,5\n"
                   "2026-09-03T15:02Z,REST API,100,90\n")
    out = run(capsys, "logs", str(log), "--threshold", "5", "--experiences", "View Issue,Edit Issue")
    assert out["downtime_minutes"] == 1 and out["intervals"] == [{"start": "2026-09-03T15:00:00Z", "end": "2026-09-03T15:01:00Z"}]
    assert out["excluded_error_minutes"] == {"REST API": 1} and out["below_threshold_error_minutes"] == 1
