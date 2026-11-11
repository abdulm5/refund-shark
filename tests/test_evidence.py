"""Customer metrics count; public incident windows only produce labeled provisional estimates."""
from datetime import datetime
from decimal import Decimal

from shark.engine import evaluate
from shark.pipeline import load_scenarios


def scenario(id):
    return next(s for s in load_scenarios() if s.id == id).model_copy(deep=True)


def test_customer_logs_price_the_claim_not_the_public_timeline():
    d = evaluate(scenario("jira-sept-claim"))
    assert d.outcome == "eligible"
    assert d.downtime_minutes == 96
    assert d.amount == Decimal("900.00")
    assert [e.counted for e in d.estimates] == [True]
    assert "request log" in d.estimate_basis


def test_public_incident_without_logs_reviews_and_estimates_each_window():
    d = evaluate(scenario("jira-sept-missing-evidence"))
    assert d.outcome == "needs_review" and d.amount == 0
    assert "request logs are missing" in d.reason
    status, postmortem = d.estimates
    assert not status.counted and not postmortem.counted
    assert status.downtime_minutes == Decimal("38.4342") and status.credit_pct is None and status.amount == 0
    assert postmortem.downtime_minutes == 130 and postmortem.credit_pct == 10 and postmortem.amount == Decimal("480.00")
    assert d.estimated_amount == Decimal("480.00") and "postmortem" in d.estimate_basis
    assert any(c.field == "credit_cap" and c.status == "review" for c in d.checks)


def test_maintenance_refusal_uses_the_real_exclusion_clause():
    d = evaluate(scenario("jira-sept-maintenance"))
    assert d.outcome == "not_eligible" and "maintenance" in d.reason.lower()
    assert (d.downtime_minutes, d.excluded_minutes) == (0, 75)


def test_real_claim_window_is_closed_in_current_time():
    s = scenario("jira-sept-claim")
    d = evaluate(s, datetime.fromisoformat("2026-10-29T12:00:00+00:00"))
    assert d.outcome == "not_eligible" and "window has closed" in d.reason and d.amount == 0


def test_unverified_customer_metrics_need_review():
    s = scenario("jira-sept-claim")
    s.incidents[1].metric_verified = False
    assert evaluate(s).outcome == "needs_review"


def test_customer_logs_must_show_a_covered_experience():
    # Applies to metrics supplied as intervals; a pinned raw log is already analyzed over covered experiences only.
    s = scenario("jira-sept-claim")
    s.incidents[1].log, s.incidents[1].log_summary = None, None
    s.incidents[1].experiences = ["Mobile app"]
    d = evaluate(s)
    assert d.outcome == "needs_review"
    assert any(c.name == "Covered experience" and c.status == "review" for c in d.checks)


def test_degraded_public_incident_is_not_priced_as_an_outage():
    s = scenario("jira-sept-missing-evidence")
    s.incidents[0].severity = "degraded_performance"
    d = evaluate(s)
    assert d.outcome == "needs_review" and d.estimates == [] and d.estimated_amount is None
    assert any("not counted as an outage" in c.detail for c in d.checks)


def test_bundled_fee_allocation_needs_review():
    s = scenario("jira-sept-claim")
    s.invoice.fee_allocation = "bundled"
    d = evaluate(s)
    assert d.outcome == "needs_review" and any(c.field == "credit_basis" for c in d.checks)


def test_annual_billing_uses_the_annual_fee_adjusted_per_month():
    # Atlassian: "If you pay your bill annually, your service credit will be based on your annual fee (adjusted for amount per month)"
    s = scenario("jira-sept-claim")
    s.account.billing_cycle = "annual"
    s.invoice.annual_fees = Decimal(108000)
    d = evaluate(s)
    assert d.outcome == "eligible" and d.affected_fees == 9000 and d.amount == Decimal("900.00")
    assert any("annual fee" in c.detail and "÷ 12" in c.detail for c in d.checks)


def test_annual_billing_without_the_annual_fee_needs_review():
    s = scenario("jira-sept-claim")
    s.account.billing_cycle = "annual"
    assert evaluate(s).outcome == "needs_review"


def test_annual_billing_needs_the_agreement_to_say_how():
    from shark.extract.citations import source_hash
    s = scenario("jira-sept-claim")
    s.account.billing_cycle, s.invoice.annual_fees = "annual", Decimal(108000)
    s.source_text = s.source_text.replace("If you pay your bill annually, your service credit will be based on your annual fee (adjusted for amount per month) for the impacted app. You receive that credit on your next annual invoice.", "")
    s.source_hash = source_hash(s.source_text)
    d = evaluate(s)
    assert d.outcome == "needs_review" and "annual" in d.reason.lower()


def test_duplicate_records_count_once():
    s = scenario("jira-sept-claim")
    s.incidents.append(s.incidents[1].model_copy(deep=True))
    assert evaluate(s).downtime_minutes == 96


def test_uptime_below_commitment_with_no_matching_tier_needs_review():
    # An extraction that drops a tier must not become a confident refusal.
    s = scenario("jira-sept-claim")
    s.terms.credit_tiers.value = [t for t in s.terms.credit_tiers.value if t.lower != Decimal("99.0")]
    d = evaluate(s)
    assert d.outcome == "needs_review"
    assert any(c.field == "credit_tiers" and "below the 99.9% commitment" in c.detail for c in d.checks)


def test_unverified_logs_do_not_answer_a_public_incident():
    from shark.models import Incident
    s = scenario("jira-sept-missing-evidence")
    claim_logs = scenario("jira-sept-claim").incidents[1]
    s.incidents.append(Incident.model_validate({**claim_logs.model_dump(), "metric_verified": False}))
    d = evaluate(s)
    assert any("request logs are missing" in c.detail and "ws0393rxcgyl" in c.detail for c in d.checks)
    assert any("postmortem" in e.label for e in d.estimates)


def test_open_public_incident_without_a_closed_window_needs_review():
    from shark.models import Incident
    s = scenario("jira-sept-claim")
    s.incidents.append(Incident(id="OPEN-1", title="Ongoing Jira outage", service="Jira", intervals=[], severity="major_outage",
                                evidence_kind="public_timeline", simulated=False, started_at=datetime.fromisoformat("2026-09-25T10:00:00+00:00")))
    d = evaluate(s)
    assert d.outcome == "needs_review"
    assert any("OPEN-1" in c.detail and "no closed window" in c.detail for c in d.checks)
    assert all(e.counted for e in d.estimates)
    s.incidents[-1].started_at = datetime.fromisoformat("2026-10-02T10:00:00+00:00")
    assert evaluate(s).outcome == "eligible"
