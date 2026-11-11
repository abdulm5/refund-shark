import re

from shark.models import Decision, Scenario

OPENINGS = {"polite": "Hello,\n\nPlease review the service-credit request below.",
            "firm": "Hello,\n\nWe are requesting the service credit supported by the agreement and evidence below."}
CLOSINGS = {"polite": "Thank you for reviewing this request. Please confirm the credit against your own monitoring.",
            "firm": "Please confirm your review and the applicable credit against your own monitoring."}


def guard_prose(text: str) -> bool:
    """Optional prose is deliberately constrained; facts never come from a model."""
    return not re.search(r"\d|[$€£%]|\b(refund|guarantee|guaranteed|owe|owed|million|hundred|thousand|one|two|three|four|five|six|seven|eight|nine|ten)\b", text, re.I) and len(text) <= 500


def draft(s: Scenario, decision: Decision, tone: str = "polite", prose: str | None = None) -> tuple[str, str]:
    if decision.outcome != "eligible":
        raise ValueError("Only an eligible decision can become an approvable claim")
    if tone not in OPENINGS:
        raise ValueError("Unsupported claim tone")
    procedure = s.terms.claim_procedure.value
    # "other" (fields the agreement does not enumerate) and "request_logs" are satisfied with a note; any other named field refuses.
    known_fields = {"account_reference", "incident_times", "service", "period", "calculation", "request_logs", "other"}
    if not procedure or set(procedure.required_fields) - known_fields:
        raise ValueError("This agreement requires additional claim fields")
    field_notes = ([("The vendor's form may ask for more fields than this draft fills; complete them when submitting.")] if "other" in procedure.required_fields else []) + (
        ["Attach the request logs that show the Downtime Minutes listed above."] if "request_logs" in procedure.required_fields else [])
    if not s.account.account_reference:
        raise ValueError("An account reference is required before drafting")
    opening = prose if prose and guard_prose(prose) else OPENINGS[tone]
    subject = procedure.subject_line or f"Service credit request: {s.product}, {s.period}"
    simulated = [p.item[0].lower() + p.item[1:] for p in s.provenance if p.kind not in {"real", "user"}]
    banner = f"DEMO CLAIM — Simulated for this demo: {'; '.join(simulated)}.\n\n" if s.simulated and simulated else (
        "DEMO CLAIM — simulated records.\n\n" if s.simulated else "")
    counted = [i for i in s.incidents if i.service == s.account.service and i.evidence_kind != "public_timeline" and i.metric_verified]
    # Cite only the vendor incidents that the counted customer logs answer.
    vendor = [i for i in s.incidents if i.evidence_kind == "public_timeline" and i.service == s.account.service
              and i.id in {c.related_incident for c in counted}]
    references = "\n".join(f"- {i.title} ({i.id}){': ' + i.source_url if i.source_url else ''}" for i in vendor) or "- None cited"
    minutes_lines = "\n".join(f"- {span.start:%Y-%m-%d %H:%M}–{span.end:%H:%M} UTC ({i.id}{', ' + ', '.join(i.experiences) if i.experiences else ''})"
                              for i in counted for span in i.intervals)
    if decision.deadline_latest and decision.deadline_latest != decision.deadline:
        deadline = f"{decision.deadline:%Y-%m-%d %H:%M} UTC (earliest reading; the clause states no time zone)"
    else:
        deadline = f"{decision.deadline:%Y-%m-%d %H:%M} UTC"
    body = f"""{banner}{opening}

Account: {s.account.account_reference}
Service: {s.product} · {s.plan}
Period: {s.period} (UTC)
Invoice: {s.invoice.id}
Remedy: future service credit

Vendor incidents
{references}

Downtime Minutes from customer request logs
{minutes_lines}

Calculation
Counted downtime: {decision.downtime_minutes.normalize()} minutes
Uptime: {decision.uptime_pct:.5f}%
Applicable credit tier: {decision.credit_pct}%
Affected service fees: {decision.currency} {decision.affected_fees:,.2f}
Requested service credit: {decision.currency} {decision.amount:,.2f}
Claim deadline: {deadline}

Agreement evidence — {s.terms.credit_tiers.section}
“{s.terms.credit_tiers.quote}”

Public status timelines alone do not establish customer-specific contractual downtime; the minutes above come from the customer's request logs.
{chr(10).join(field_notes) + chr(10) if field_notes else ''}
{CLOSINGS[tone]}"""
    return subject, body


def dispute_draft(s: Scenario, decision: Decision, claim_id: str, requested, awarded, deadline: str, quote: str | None) -> str:
    """A revisit request: every figure comes from the stored claim and decision."""
    rule = f"Your compensation process says: “{quote}”\n\n" if quote else ""
    return f"""{'DEMO REVISIT REQUEST — simulated award and submission.\n\n' if s.simulated else ''}Hello,

We received a service credit of {decision.currency} {awarded:,.2f} for request {claim_id} ({s.product} · {s.plan}, period {s.period}).
We requested {decision.currency} {requested:,.2f}, so the award is {decision.currency} {requested - awarded:,.2f} short. {rule}We ask you to revisit the claim before {deadline}.

Calculation (unchanged)
Counted downtime: {decision.downtime_minutes.normalize()} minutes
Uptime: {decision.uptime_pct:.5f}%
Applicable credit tier: {decision.credit_pct}%
Affected service fees: {decision.currency} {decision.affected_fees:,.2f}
Requested service credit: {decision.currency} {requested:,.2f}

Please confirm the revised credit against your own monitoring."""
