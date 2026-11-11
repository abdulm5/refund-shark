from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from decimal import Decimal, ROUND_HALF_UP

from shark.extract.citations import normalize, source_hash, term_status, validate_rules, verify_citations
from shark.models import Check, Decision, Estimate, Scenario
from .intervals import clip, merge, microseconds, minutes, month_bounds, subtract

# Terms whose confirmed absence has a defined meaning: no minimum, no cap, none listed.
OPTIONAL_TERMS = {"min_credit", "credit_cap", "exclusions", "prerequisites"}
# Atlassian's compensation procedure: the per-month adjustment for annual billing. Applied only where the agreement says it.
ANNUAL_RULE = "your service credit will be based on your annual fee (adjusted for amount per month)"
# Civil time zones span UTC+14 to UTC-12: the envelope for a deadline clause that names no zone.
EARLIEST_ZONE, LATEST_ZONE = timezone(timedelta(hours=14)), timezone(timedelta(hours=-12))


def deadline_in(period_end: datetime, days: int, zone) -> datetime:
    """Last instant of the final allowed day, counting days from the period's end date."""
    return (datetime.combine(period_end.date() + timedelta(days=days), time(0), tzinfo=zone)
            - timedelta(microseconds=1)).astimezone(timezone.utc)


def deadline_bounds(period_end: datetime, rule) -> tuple[datetime, datetime] | None:
    if rule.kind != "days_after_period":
        return None
    if rule.timezone is None:
        return deadline_in(period_end, rule.value, EARLIEST_ZONE), deadline_in(period_end, rule.value, LATEST_ZONE)
    try:
        zone = timezone.utc if rule.timezone == "UTC" else ZoneInfo(rule.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        return None
    deadline = deadline_in(period_end, rule.value, zone)
    return deadline, deadline


def evaluate(s: Scenario, as_of: datetime | None = None) -> Decision:
    now = as_of or s.as_of
    terms = s.terms
    result = Decision(outcome="needs_review", reason="Evidence needs review", evaluated_at=now,
                      affected_fees=s.invoice.affected_fees, currency=s.invoice.currency,
                      evidence_quality="simulated" if s.simulated else "public estimate")

    def check(name: str, status: str, detail: str, field: str | None = None):
        result.checks.append(Check(name=name, status=status, detail=detail, field=field))

    def finish(outcome: str, reason: str):
        result.outcome, result.reason = outcome, reason
        if outcome != "eligible":
            result.amount = Decimal("0.00")
        return result

    result.citations = verify_citations(terms, s.source_text)
    if source_hash(s.source_text) != s.source_hash:
        check("Source integrity", "review", "Source snapshot changed; re-extract and verify its clauses.")
        return finish("needs_review", "Source snapshot has changed")
    result.term_status = term_status(terms, s.source_text)
    for name, status in result.term_status.items():
        if status in {"missing", "unverified"}:
            check("Contract evidence", "review", f"Missing or unverified field: {name}", name)
        elif status == "absent" and name not in OPTIONAL_TERMS:
            check("Contract evidence", "review", f"The agreement states no {name.replace('_', ' ')}; this cannot be evaluated.", name)
    if any(c.status == "review" for c in result.checks):
        return finish("needs_review", "Required contract terms could not be verified")
    verified = sum(status == "verified" for status in result.term_status.values())
    absent = [name for name, status in result.term_status.items() if status == "absent"]
    check("Contract evidence", "pass", f"{verified} quoted fields match the saved source."
          + (f" Confirmed absent from the agreement: {', '.join(absent)}." if absent else ""))
    errors = validate_rules(terms)
    if errors:
        check("Contract rules", "review", "; ".join(errors))
        return finish("needs_review", "Contract rules require review")

    if terms.vendor.value != s.invoice.vendor or s.invoice.customer_id != s.account.customer_id or s.invoice.period != s.period:
        check("Invoice match", "review", "Invoice vendor, customer, or billing period does not match.")
        return finish("needs_review", "Invoice does not match this claim")
    if s.account.plan != terms.plan.value or s.account.service not in terms.covered_services.value:
        check("Coverage", "fail", "This product or subscription plan is not covered.", "covered_services")
        return finish("not_eligible", "Product or plan is outside the agreement")
    if s.invoice.service != s.account.service or terms.credit_basis.value != "affected_service" or s.invoice.currency != "USD":
        check("Invoice basis", "review", "The fee allocation or currency needs a supported pricing adapter.", "credit_basis")
        return finish("needs_review", "Unsupported invoice basis or currency")
    if s.invoice.fee_allocation != "standalone":
        check("Invoice basis", "review", "This product's share of a bundled or unknown fee is set by the vendor; the affected fees cannot be priced yet.", "credit_basis")
        return finish("needs_review", "The affected product's fee allocation is unknown")
    affected = s.invoice.affected_fees
    if s.account.billing_cycle == "annual":
        if s.invoice.annual_fees is None:
            check("Invoice basis", "review", "Annual billing: the product's annual fee is needed to price the credit.", "credit_basis")
            return finish("needs_review", "The annual fee for this product is missing")
        if normalize(ANNUAL_RULE) not in normalize(s.source_text):
            check("Invoice basis", "review", "Annual billing needs the agreement's own rule for crediting an annual fee.", "credit_basis")
            return finish("needs_review", "This agreement does not say how annual fees are credited")
        affected = s.invoice.annual_fees / 12
        check("Annual billing", "pass", f"Credits use the annual fee adjusted per month: ${s.invoice.annual_fees:,.2f} ÷ 12 = ${affected:,.2f}. "
              f"The compensation procedure says “{ANNUAL_RULE}”.", "credit_basis")
    elif s.account.billing_cycle != "monthly":
        check("Invoice basis", "review", "The billing cycle is unknown; the monthly fee basis cannot be applied.", "credit_basis")
        return finish("needs_review", "This billing cycle needs another pricing adapter")
    result.affected_fees = affected
    check("Product and invoice", "pass", f"{s.product} · {s.plan} · {s.invoice.id}", "covered_services")

    if terms.measurement_period.value != "calendar_month" or terms.timezone.value != "UTC":
        check("Measurement period", "review", "Only UTC calendar-month calculations are currently supported.", "measurement_period")
        return finish("needs_review", "This measurement period needs another calculation adapter")
    start, end = month_bounds(s.period)
    total_us = microseconds(start, end)
    result.period_minutes = Decimal(total_us) / Decimal(60_000_000)
    window = terms.claim_window.value
    bounds = deadline_bounds(end, window)
    if bounds:
        result.deadline, result.deadline_latest = bounds
    else:
        check("Claim window", "review", "Deadline rule or timezone requires review.", "claim_window")
    if now < end:
        check("Complete period", "review", "The billing period has not ended; this remains a provisional estimate.")

    # Customer metrics (or authored stand-ins) are contractual evidence. Public incidents are
    # discovery: unless customer metrics answer them, they only produce provisional estimates.
    exclusions = terms.exclusions.value or []
    excludes_maintenance = any(e.kind == "scheduled_maintenance" for e in exclusions)
    maintenance = clip([m.interval for m in s.maintenance if m.service == s.account.service and m.exclusion_confirmed] if excludes_maintenance else [], start, end)
    records = [i for i in s.incidents if i.service == s.account.service]
    measured = [i for i in records if i.evidence_kind != "public_timeline"]
    # Only verified customer metrics can answer a public incident.
    answered = {i.related_incident for i in measured if i.related_incident and i.metric_verified}

    def in_period(i) -> bool:
        if clip(i.intervals + [span for w in i.context_windows for span in w.intervals], start, end):
            return True
        # No closed window (an open or incomplete timeline): keep it unless it began after the period.
        return not i.intervals and i.severity != "operational" and (i.started_at is None or i.started_at < end)

    discovery = [i for i in records if i.evidence_kind == "public_timeline" and i.id not in answered and in_period(i)]
    counted = [span for i in measured if i.metric_verified for span in i.intervals]

    cap = terms.credit_cap.value
    cap_fees = None if cap is None else affected if cap.basis == "affected_period_fees" else (
        s.account.receiving_invoice_fees if cap.basis == "receiving_invoice" else None)

    def price(label: str, is_counted: bool, spans) -> tuple[Estimate, Decimal, Decimal, bool]:
        raw = merge(clip(spans, start, end))
        effective = subtract(raw, maintenance)
        down_us = sum(microseconds(i.start, i.end) for i in effective)
        # Compare cross-multiplied Decimals, never a rounded displayed uptime.
        numerator = Decimal(100) * Decimal(total_us - down_us)
        tier = next((t for t in terms.credit_tiers.value
                     if (numerator >= t.lower * total_us if t.lower_inclusive else numerator > t.lower * total_us)
                     and (numerator <= t.upper * total_us if t.upper_inclusive else numerator < t.upper * total_us)), None)
        credit = affected * tier.credit_pct / 100 if tier else Decimal(0)
        if tier and cap is not None and cap_fees is not None:
            credit = min(credit, cap_fees * cap.pct / 100)
        estimate = Estimate(label=label, counted=is_counted, downtime_minutes=minutes(effective), uptime_pct=numerator / total_us,
                            credit_pct=tier.credit_pct if tier else None, amount=credit.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
        below_commitment = numerator < terms.uptime_commitment_pct.value * total_us
        return estimate, minutes(raw) - estimate.downtime_minutes, credit, below_commitment

    supported, supported_excluded, supported_credit, below_commitment = price("; ".join(dict.fromkeys(i.interval_basis for i in measured if i.metric_verified)) or "No customer metrics supplied", True, counted)
    if any(i.metric_verified for i in measured):
        result.estimates.append(supported)
    unverified = [i for i in measured if not i.metric_verified]
    if unverified:
        result.estimates.append(price("Unverified: " + "; ".join(dict.fromkeys(i.interval_basis for i in unverified)), False,
                                      counted + [span for i in unverified for span in i.intervals])[0])
    priced = [i for i in discovery if i.severity == "major_outage" and i.intervals]
    if priced:
        status_spans = counted + [span for i in priced for span in i.intervals]
        result.estimates.append(price(" + ".join(dict.fromkeys(i.interval_basis for i in priced)), False, status_spans)[0])
        windows = [w for i in priced for w in i.context_windows]
        if windows:
            result.estimates.append(price(" + ".join(dict.fromkeys(w.label for w in windows)), False,
                                          status_spans + [span for w in windows for span in w.intervals])[0])
    basis = result.estimates[-1] if result.estimates else supported
    excluded = supported_excluded if basis is supported else Decimal(0)
    result.downtime_minutes, result.excluded_minutes, result.uptime_pct, result.credit_pct = basis.downtime_minutes, excluded, basis.uptime_pct, basis.credit_pct
    check("Downtime calculation", "pass", f"{supported.downtime_minutes.normalize()} counted minutes from customer evidence; {supported_excluded.normalize()} minutes excluded.", "downtime_definition")

    metric = terms.downtime_definition.value
    if metric.kind not in {"availability_minutes", "request_error_rate"}:
        result.estimates = []
        check("Measurement evidence", "review", "This contract needs a metric that cannot be inferred from public outage duration.", "downtime_definition")
        return finish("needs_review", "The contract requires another metric; a credit cannot be priced from outage duration")
    if metric.kind == "request_error_rate" and (metric.interval_seconds != 60 or metric.error_threshold_pct is None):
        check("Measurement evidence", "review", "The request-error measurement rule needs a supported adapter.", "downtime_definition")
    for incident in discovery:
        if not incident.intervals:
            check("Customer evidence", "review", f"{incident.title} ({incident.id}) has no closed window in its public timeline; it may still be open. "
                  "Customer request logs are needed to determine Downtime Minutes.", "downtime_definition")
        elif incident.severity == "major_outage":
            check("Customer evidence", "review", f"Customer request logs are missing for {incident.title} ({incident.id}). "
                  "Public status timelines do not establish this customer's Downtime Minutes.", "downtime_definition")
        else:
            check("Customer evidence", "review", f"{incident.title} ({incident.id}) was reported as {incident.severity.replace('_', ' ')}, which is not counted as an outage. "
                  "Customer request logs are needed to determine Downtime Minutes.", "downtime_definition")
    for incident in measured:
        if not incident.metric_verified:
            check("Customer evidence", "review", f"{incident.title} has not been verified against the per-minute error threshold.", "downtime_definition")
        elif metric.covered_experiences and not incident.log_summary and not set(incident.experiences) & set(metric.covered_experiences):
            check("Covered experience", "review", f"{incident.title} does not show errors on a covered experience ({', '.join(metric.covered_experiences)}).", "downtime_definition")
    if not discovery and measured and all(i.metric_verified for i in measured):
        check("Customer evidence", "pass", "Customer metric intervals are supplied" + (" (simulated for this demo)." if any(i.simulated for i in measured) else "."), "downtime_definition")
    for incident in measured:
        summary = incident.log_summary
        if summary and summary.minutes_covered < summary.period_minutes:
            check("Period coverage", "review", f"The request logs for {incident.id} cover {summary.minutes_covered} of {summary.period_minutes} minutes in the period.")
    if not s.account.history_complete and not any(i.log_summary for i in measured):  # a log reports its own coverage above
        check("Period coverage", "review", "The evidence does not prove coverage for the whole period.")
    if any(not e.machine_checkable for e in exclusions) and not s.account.non_machine_exclusions_reviewed:
        check("Exclusions", "review", "Exclusions that code cannot check (for example customer equipment or third-party products) need a customer review.", "exclusions")
    if any(m.service == s.account.service and not m.exclusion_confirmed for m in s.maintenance):
        check("Maintenance", "review", "A maintenance window has not been confirmed as an applicable exclusion.", "exclusions")

    for prerequisite in terms.prerequisites.value or []:
        known = s.account.prerequisites.get(prerequisite)
        check(prerequisite.replace("_", " ").capitalize(), "pass" if known is True else "fail" if known is False else "review",
              "Requirement satisfied in supplied evidence." if known is True else "Requirement was not satisfied." if known is False else "Supporting evidence is missing.", "prerequisites")

    if any(e.credit_pct for e in result.estimates):
        if cap is None:
            check("Credit cap", "pass", "No cap is stated in the agreement.", "credit_cap")
        elif cap_fees is None:
            check("Credit cap", "review", "The receiving invoice or cap basis is unavailable.", "credit_cap")
    if result.estimates:
        result.estimated_amount, result.estimate_basis = basis.amount, basis.label
    elif not discovery:
        result.estimated_amount = supported.amount  # nothing to price: no counted minutes
    provisional = not basis.counted
    result.calculation = [
        {"label": "Minutes in billing period", "value": str(result.period_minutes), "detail": f"{s.period} · UTC"},
        {"label": "Excluded maintenance", "value": str(result.excluded_minutes), "detail": "Confirmed exclusion windows"},
        {"label": "Provisional downtime" if provisional else "Counted downtime", "value": str(result.downtime_minutes), "detail": basis.label},
        {"label": "Calculated uptime", "value": f"{result.uptime_pct:.5f}%", "detail": "100 × (1 − downtime / period)"},
        {"label": "Credit tier", "value": f"{result.credit_pct or 0}%", "detail": "Compared against unrounded uptime"},
        {"label": "Affected service fees", "value": f"${affected:,.2f}", "detail": s.invoice.id + (" · annual fee ÷ 12" if s.account.billing_cycle == "annual" else "")},
        {"label": "Provisional service credit" if provisional else "Potential service credit", "value": f"${basis.amount:,.2f}",
         "detail": "Not supported until customer evidence is supplied" if provisional else "Tier applied; contractual cap applied when known"},
    ]
    if result.deadline:
        earliest, latest = result.deadline, result.deadline_latest
        stamp = "%b %d, %Y %H:%M UTC"
        if now > latest:
            check("Claim deadline", "fail", f"The request window closed on {latest:%b %d, %Y} UTC.", "claim_window")
            return finish("not_eligible", "The claim window has closed")
        if now > earliest:
            check("Claim deadline", "review", f"The clause states no time zone, so the deadline falls between {earliest:{stamp}} and {latest:{stamp}}. It may already have passed.", "claim_window")
        elif earliest == latest:
            check("Claim deadline", "pass", f"Submit by {earliest:{stamp}}.", "claim_window")
        else:
            reading = deadline_in(end, window.value, timezone.utc)
            check("Claim deadline", "pass", f"Submit by {earliest:{stamp}}. The clause states no time zone, so this is the earliest reading (UTC+14); "
                  f"read in UTC it is {reading:{stamp}}, and the latest reading (UTC−12) is {latest:{stamp}}.", "claim_window")
    failed = next((c for c in result.checks if c.status == "fail"), None)
    if failed:
        return finish("not_eligible", f"{failed.name}: {failed.detail}")
    review = next((c for c in result.checks if c.status == "review"), None)
    if review:
        return finish("needs_review", review.detail)
    if supported.credit_pct is None:
        if supported_excluded and not supported.downtime_minutes:
            check("Maintenance exclusion", "pass", "All supplied downtime falls within confirmed excluded maintenance.", "exclusions")
            return finish("not_eligible", "Scheduled maintenance is excluded from this agreement")
        if below_commitment:
            # A table that leaves this uptime uncovered is more likely a bad extraction than a contract with no remedy.
            check("Credit table", "review", f"Uptime of {supported.uptime_pct:.5f}% is below the {terms.uptime_commitment_pct.value}% commitment, "
                  "but no credit tier covers it. Check the credit table.", "credit_tiers")
            return finish("needs_review", "Uptime is below the commitment but no credit tier applies")
        return finish("not_eligible", "Uptime meets the agreement; no credit tier applies")
    minimum = terms.min_credit.value
    if minimum is None:
        check("Minimum credit", "pass", "No minimum credit amount is stated in the agreement.", "min_credit")
    elif supported_credit < minimum.amount or (not minimum.inclusive and supported_credit == minimum.amount):
        return finish("not_eligible", "The calculated credit does not meet the minimum amount")
    if supported.amount == 0:
        check("Credit amount", "fail", "The tier and cap leave a credit of $0.00 after rounding to cents.", "credit_tiers")
        return finish("not_eligible", "The calculated credit is $0.00")
    result.amount = supported.amount
    return finish("eligible", "Supplied evidence supports a service-credit request")
