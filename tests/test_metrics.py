"""Downtime Minutes from per-minute request logs, exactly as Atlassian's SLA defines them.

The first four cases are the SLA's own worked examples (Appendix B, "Error Rate").
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from shark.engine.metrics import Sample, downtime_from_samples

T0 = datetime(2026, 9, 3, 15, 0, tzinfo=timezone.utc)
COVERED = ["View Issue", "Create Issue", "Edit Issue", "View Board"]


def minute(offset, experience, requests, errors):
    return Sample(minute=T0 + timedelta(minutes=offset), experience=experience, requests=requests, errors=errors)


def analyze(samples):
    return downtime_from_samples(samples, threshold_pct=Decimal(5), covered=COVERED)


def test_sla_examples():
    result = analyze([minute(0, "View Issue", 100, 100),   # completely inoperable: 100% counts
                      minute(1, "View Issue", 100, 10),    # 10 of 100 fail: 10% counts
                      minute(2, "View Issue", 100, 1),     # 1 of 100 fail: 1% does not count
                      minute(3, "View Issue", 0, 0)])      # no requests: 0% does not count
    assert [(i.start, i.end) for i in result.intervals] == [(T0, T0 + timedelta(minutes=2))]
    assert result.downtime_minutes == 2 and result.zero_request_minutes == 1


def test_exactly_five_percent_does_not_count():
    assert analyze([minute(0, "Edit Issue", 100, 5)]).downtime_minutes == 0
    assert analyze([minute(0, "Edit Issue", 1000, 51)]).downtime_minutes == 1


def test_rate_is_over_all_covered_experiences_in_the_minute():
    # 4/50 + 2/50 = 6 of 100 covered requests: a Downtime Minute, though neither alone exceeds 5% by much
    assert analyze([minute(0, "View Issue", 50, 4), minute(0, "Edit Issue", 50, 2)]).downtime_minutes == 1
    # 3/50 + 0/50 = 3%: not a Downtime Minute even though View Issue alone was 6%
    assert analyze([minute(0, "View Issue", 50, 3), minute(0, "Edit Issue", 50, 0)]).downtime_minutes == 0


def test_errors_outside_covered_experiences_are_excluded():
    result = analyze([minute(0, "REST API", 100, 90), minute(1, "REST API", 100, 90), minute(0, "View Issue", 100, 0)])
    assert result.downtime_minutes == 0 and result.excluded_error_minutes == {"REST API": 2}


def test_consecutive_minutes_merge_and_coverage_is_counted():
    samples = [minute(m, "View Issue", 20, 20 if 10 <= m < 13 else 0) for m in range(30)]
    result = analyze(samples)
    assert [(i.start, i.end) for i in result.intervals] == [(T0 + timedelta(minutes=10), T0 + timedelta(minutes=13))]
    assert result.minutes_covered == 30 and result.below_threshold_error_minutes == 0


def test_claim_minutes_are_computed_from_the_request_log():
    import json
    from conftest import logs, scenario
    from shark.pipeline import ROOT
    raw = next(s for s in json.loads((ROOT / "data/fixtures/scenarios.json").read_text()) if s["id"] == "jira-sept-claim")
    stored = next(i for i in raw["incidents"] if i["evidence_kind"] == "customer_metrics")
    assert stored["intervals"] == [] and stored["log"]["path"].endswith(".csv.gz")  # nothing hand-placed
    record = logs(scenario("jira-sept-claim"))
    assert [(i.start.strftime("%H:%M"), i.end.strftime("%H:%M")) for i in record.intervals] == [("15:04", "16:22"), ("16:30", "16:48")]
    summary = record.log_summary
    assert summary.downtime_minutes == 96 and summary.minutes_covered == summary.period_minutes == 43200
    assert summary.excluded_error_minutes.get("REST API", 0) > 0 and summary.below_threshold_error_minutes > 0
    assert set(record.experiences) == {"View Issue", "Edit Issue"}


def test_a_changed_log_is_refused(tmp_path):
    from conftest import scenario
    from shark.engine.metrics import apply_logs
    s = scenario("jira-sept-claim")
    record = next(i for i in s.incidents if i.log)
    record.log.sha256 = "0" * 64
    import pytest
    with pytest.raises(ValueError, match="changed"):
        apply_logs(s)


def test_partial_log_coverage_needs_review():
    from conftest import logs, scenario
    from shark.engine import evaluate
    s = scenario("jira-sept-claim")
    logs(s).log_summary.minutes_covered = 40000
    d = evaluate(s)
    assert d.outcome == "needs_review" and any("cover 40000 of 43200" in c.detail for c in d.checks)
