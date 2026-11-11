from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from hypothesis import given, strategies as st

from conftest import logs, scenario
from shark.engine import evaluate
from shark.engine.intervals import clip, merge, minutes, month_bounds
from shark.extract.citations import source_hash, verify_citations
from shark.models import Cited, Interval, Maintenance, Minimum, Tier


def dt(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def outage(case, duration):
    start = month_bounds(case.period)[0] + timedelta(days=5)
    logs(case).intervals = [Interval(start=start, end=start + timedelta(microseconds=int(Decimal(str(duration)) * 60_000_000)))]
    return case


def with_minimum(case, amount, inclusive):
    """Append an authored minimum clause; Atlassian's SLA states none."""
    quote = f"Authored test clause: a service credit must be {'at least' if inclusive else 'greater than'} ${amount}."
    case.source_text += f"\n{quote}\n"
    case.source_hash = source_hash(case.source_text)
    case.terms.min_credit = Cited(value=Minimum(amount=Decimal(amount), inclusive=inclusive), quote=quote, section="Test clause")
    return case


def test_no_incidents(case):
    case.incidents = []
    d = evaluate(case)
    assert d.outcome == "not_eligible"
    assert d.uptime_pct == 100


@pytest.mark.parametrize("authorization,outcome,amount", [(None, "needs_review", "0.00"), (False, "not_eligible", "0.00"), (True, "eligible", "900.00")])
def test_atlassian_requester_must_be_authorized(case, authorization, outcome, amount):
    case.account.prerequisites["authorized_requester"] = authorization
    result = evaluate(case)
    assert (result.outcome, result.amount) == (outcome, Decimal(amount))
    assert verify_citations(case.terms, case.source_text)["prerequisites"]


def test_atlassian_missing_requester_authorization_requires_review(case):
    del case.account.prerequisites["authorized_requester"]
    assert evaluate(case).outcome == "needs_review"


@pytest.mark.parametrize("duration,expected", [("43.199999", "not_eligible"), ("43.2", "not_eligible"), ("43.200001", "eligible")])
def test_exact_99_9_boundary(case, duration, expected):
    assert evaluate(outage(case, duration)).outcome == expected


@pytest.mark.parametrize("duration,credit", [("432", "10"), ("432.000001", "25"), ("2160", "25"), ("2160.000001", "50")])
def test_inclusive_lower_tier_boundaries(case, duration, credit):
    # 432 minutes is exactly 99.0% of September; 2,160 minutes is exactly 95.0%.
    assert evaluate(outage(case, duration)).credit_pct == Decimal(credit)


@pytest.mark.parametrize("duration,credit", [("21.6", None), ("21.600001", "5")])
def test_exclusive_upper_boundary_of_enterprise_tier(case, duration, credit):
    case.terms.credit_tiers.value.append(Tier(lower=Decimal("99.9"), upper=Decimal("99.95"), credit_pct=Decimal(5)))
    d = evaluate(outage(case, duration))
    assert d.credit_pct == (Decimal(credit) if credit else None)


def test_worked_example(case):
    case.invoice.affected_fees = Decimal(2000)
    result = evaluate(outage(case, 120))
    assert result.outcome == "eligible"
    assert result.amount == Decimal("200.00")
    assert result.downtime_minutes == 120


def test_overlap_is_counted_once(case):
    logs(case).intervals = [Interval(start=dt("2026-09-05T10:00:00Z"), end=dt("2026-09-05T10:40:00Z")), Interval(start=dt("2026-09-05T10:20:00Z"), end=dt("2026-09-05T11:00:00Z"))]
    assert evaluate(case).downtime_minutes == 60


def test_cross_month_split():
    interval = Interval(start=dt("2026-10-31T23:30:00Z"), end=dt("2026-11-01T00:30:00Z"))
    assert minutes(clip([interval], *month_bounds("2026-10"))) == 30
    assert minutes(clip([interval], *month_bounds("2026-11"))) == 30


def test_downtime_outside_the_period_is_not_counted(case):
    logs(case).intervals = [Interval(start=dt("2026-08-31T23:00:00Z"), end=dt("2026-09-01T00:30:00Z"))]
    assert evaluate(case).downtime_minutes == 30


@pytest.mark.parametrize("period,days", [("2024-02", 29), ("2025-02", 28), ("2028-02", 29), ("2026-04", 30), ("2026-12", 31)])
def test_actual_month_length(period, days):
    start, end = month_bounds(period)
    assert (end - start).days == days


def test_cap_on_receiving_invoice(case):
    case.terms.credit_cap.value.pct = Decimal(5)
    assert evaluate(case).amount == Decimal("450.00")


def test_receiving_invoice_cap_needs_the_invoice(case):
    case.account.receiving_invoice_fees = None
    assert evaluate(case).outcome == "needs_review"
    case.account.receiving_invoice_fees = Decimal(100)
    assert evaluate(case).amount == Decimal(100)


def test_affected_period_cap(case):
    case.terms.credit_cap.value.basis = "affected_period_fees"
    case.terms.credit_cap.value.pct = Decimal(5)
    assert evaluate(case).amount == Decimal("450.00")


def test_maintenance_refusal():
    d = evaluate(scenario("jira-sept-maintenance"))
    assert d.outcome == "not_eligible"
    assert d.excluded_minutes == 75
    assert d.downtime_minutes == 0
    assert any(c.field == "exclusions" for c in d.checks)


def test_partial_maintenance(case):
    span = logs(case).intervals[0]
    case.maintenance = [Maintenance(service=case.product, interval=Interval(start=span.start, end=span.start + timedelta(minutes=30)), exclusion_confirmed=True)]
    d = evaluate(case)
    assert d.downtime_minutes == 66
    assert d.excluded_minutes == 30


def test_unconfirmed_maintenance_is_not_excluded(case):
    span = logs(case).intervals[0]
    case.maintenance = [Maintenance(service=case.product, interval=span, exclusion_confirmed=False)]
    d = evaluate(case)
    assert d.downtime_minutes == 96 and d.outcome == "needs_review"


@pytest.mark.parametrize("fees,inclusive,expected", [("8", False, "not_eligible"), ("10", False, "not_eligible"), ("10.10", False, "eligible"), ("10", True, "eligible")])
def test_minimum_credit_boundary(case, fees, inclusive, expected):
    with_minimum(case, "1.00", inclusive)
    case.invoice.affected_fees = Decimal(fees)
    case.account.receiving_invoice_fees = Decimal(fees)
    assert evaluate(case).outcome == expected


def test_deadline_and_inclusivity(case):
    case.terms.claim_window.value.timezone = "UTC"
    deadline = evaluate(case).deadline
    assert deadline == dt("2026-10-15T23:59:59.999999Z")
    assert evaluate(case, deadline).outcome == "eligible"
    d = evaluate(case, deadline + timedelta(microseconds=1))
    assert d.outcome == "not_eligible"
    assert "window has closed" in d.reason
    assert d.amount == 0


def test_unknown_deadline_timezone_uses_earliest_and_latest_readings(case):
    # The clause gives a day count but no time zone: show the full envelope, never guess one.
    case.terms.claim_window.value.timezone = None
    d = evaluate(case)
    earliest, latest = dt("2026-10-15T09:59:59.999999Z"), dt("2026-10-16T11:59:59.999999Z")
    assert (d.deadline, d.deadline_latest) == (earliest, latest)
    assert d.outcome == "eligible"
    note = next(c for c in d.checks if c.field == "claim_window")
    assert note.status == "pass" and "time zone" in note.detail and "Oct 15, 2026 23:59 UTC" in note.detail
    assert evaluate(case, earliest).outcome == "eligible"
    for ambiguous in (earliest + timedelta(microseconds=1), latest):
        d = evaluate(case, ambiguous)
        assert d.outcome == "needs_review"
        assert any(c.field == "claim_window" and c.status == "review" for c in d.checks)
    d = evaluate(case, latest + timedelta(microseconds=1))
    assert d.outcome == "not_eligible" and "window has closed" in d.reason


def test_named_deadline_timezone(case):
    case.terms.claim_window.value.timezone = "America/Los_Angeles"
    d = evaluate(case)
    assert d.deadline == d.deadline_latest == dt("2026-10-16T06:59:59.999999Z")


def test_unsupported_deadline_rule(case):
    case.terms.claim_window.value.kind = "billing_cycles_after"
    d = evaluate(case)
    assert d.outcome == "needs_review" and d.deadline is None


def test_failed_quote(case):
    case.terms.credit_tiers.quote = "This quote is not in the document."
    d = evaluate(case)
    assert d.outcome == "needs_review"
    assert any(c.field == "credit_tiers" for c in d.checks)


def test_missing_evidence_never_approvable():
    d = evaluate(scenario("jira-sept-missing-evidence"))
    assert d.outcome == "needs_review"
    assert d.amount == 0
    assert d.estimated_amount == 480


@pytest.mark.parametrize("known,outcome", [(None, "needs_review"), (False, "not_eligible"), (True, "eligible")])
def test_prerequisite_states(case, known, outcome):
    case.account.prerequisites["support_ticket_during_incident"] = known
    assert evaluate(case).outcome == outcome


def test_unknown_coverage_not_100_percent_proof(case):
    case.incidents = []
    case.account.history_complete = False
    assert evaluate(case).outcome == "needs_review"


def test_source_mutation_invalidates(case):
    case.source_text += " Altered text"
    assert evaluate(case).reason == "Source snapshot has changed"


def test_quote_presence_is_not_semantic_accuracy(case):
    case.terms.uptime_commitment_pct.value = Decimal("98")
    assert verify_citations(case.terms, case.source_text)["uptime_commitment_pct"] is True
    # The evaluation's field-value comparison must catch this independent of quote checking.
    assert case.terms.uptime_commitment_pct.value != scenario("jira-sept-claim").terms.uptime_commitment_pct.value


def test_overlapping_tiers_block(case):
    next(t for t in case.terms.credit_tiers.value if t.lower == 95).upper = Decimal("99.5")
    assert evaluate(case).outcome == "needs_review"


def test_currency_and_plan(case):
    case.invoice.currency = "EUR"
    assert evaluate(case).outcome == "needs_review"
    case.invoice.currency = "USD"
    case.account.plan = "Free"
    assert evaluate(case).outcome == "not_eligible"


def test_open_period_is_provisional(case):
    assert evaluate(case, dt("2026-09-20T00:00:00Z")).outcome == "needs_review"


def test_unsupported_metric_is_unpriced():
    d = evaluate(scenario("cloudflare"))
    assert d.outcome == "needs_review"
    assert d.estimated_amount is None


def test_quarterly_measurement_needs_review():
    d = evaluate(scenario("github"))
    assert d.outcome == "needs_review" and any(c.field == "measurement_period" for c in d.checks)


@given(st.lists(st.tuples(st.integers(0, 1000), st.integers(1, 100)), min_size=0, max_size=30))
def test_interval_union_invariants(spans):
    epoch = dt("2026-09-01T00:00:00Z")
    intervals = [Interval(start=epoch + timedelta(minutes=start), end=epoch + timedelta(minutes=start + length)) for start, length in spans]
    assert minutes(intervals) == minutes(list(reversed(intervals))) == minutes(intervals + intervals)
    assert minutes(intervals) <= sum((Decimal(length) for _, length in spans), Decimal(0))
    merged = merge(intervals)
    assert all(a.end < b.start for a, b in zip(merged, merged[1:]))


@given(st.lists(st.tuples(st.integers(-2000, 45000), st.integers(1, 5000)), max_size=20))
def test_counted_downtime_stays_within_the_period(spans):
    case = scenario("jira-sept-claim")
    epoch = dt("2026-09-01T00:00:00Z")
    logs(case).intervals = [Interval(start=epoch + timedelta(minutes=a), end=epoch + timedelta(minutes=a + n)) for a, n in spans]
    d = evaluate(case)
    assert 0 <= d.downtime_minutes <= d.period_minutes
