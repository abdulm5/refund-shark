"""Generate the demo scenarios in data/fixtures/scenarios.json.

The three Atlassian cases cite verbatim excerpts of Atlassian's public SLA and compensation
procedure (data/sources/). The September 3 incident and its timestamps are real and come from
docs/research/real-incidents.json. Every customer, invoice, request log, ticket, and maintenance
notice is simulated and labeled as such. The GitHub and Cloudflare cases use authored agreement
text to show where the engine stops; they are not those vendors' terms.
"""
import csv
import gzip
import hashlib
import io
import json
import random
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shark.extract.citations import source_hash  # noqa: E402
from shark.extract.excerpts import compose_source  # noqa: E402
from shark.engine.metrics import downtime_from_samples, read_log  # noqa: E402
from shark.incidents.statuspage import normalize_incident  # noqa: E402
from shark.models import Customer, Incident, Interval, Provenance, Scenario, Window  # noqa: E402

AS_OF = "2026-10-08T12:00:00Z"  # historical replay clock: inside the September claim window
CURRENT_AS_OF = "2026-10-29T12:00:00Z"  # research date: October is still open
SLA_URL = "https://www.atlassian.com/legal/sla"
# Statuspage components whose outage maps to Jira's covered experiences (view, create, edit issues).
JIRA_COVERED_COMPONENTS = {"b01bgtvb4q2j": "Create and edit", "jmgfbtr0k28j": "Viewing content"}


def cite(value, quote, section):
    return {"value": value, "quote": quote, "section": section}


def atlassian_jira_premium_terms():
    """AI-checked demo terms; human reference attestation remains pending. See docs/AI_REFERENCE_REVIEW.md."""
    commitment = "Cloud Plan\nService Level Commitment\nPremium\n99.9%"
    exclusions = [
        ("customer_breach", "Customer is in breach of the Agreement"),
        ("not_provisioned", "Customer has not provisioned the relevant Cloud Product"),
        ("unauthorized_use", "Use of the Cloud Products not authorized under the Agreement"),
        ("force_majeure", "Force majeure or factors outside Atlassian's reasonable control, including internet access"),
        ("customer_infrastructure", "Customer equipment, software, network connections or other infrastructure"),
        ("customer_data", "Customer Data or Customer Materials"),
        ("third_party_products", "Third-Party Products"),
        ("emergency_maintenance", "Reasonable emergency maintenance under the Atlassian Maintenance Policy; applicability needs review"),
        ("sandbox_or_free_plan", "Sandbox instances, Free or Beta Products; the compensation procedure also excludes trial instances"),
        ("excluded_features", "Features excluded from the commitment in the applicable Documentation"),
    ]
    return {
        "vendor": cite("Atlassian", "Atlassian must provide the following monthly uptime percentage to Customer", "§1 Service Level Commitment"),
        "covered_services": cite(["Jira"], "Jira (Premium and Enterprise)", "Appendix A"),
        "plan": cite("Premium", commitment, "§1 Service Level Commitment"),
        "uptime_commitment_pct": cite("99.9", commitment, "§1 Service Level Commitment"),
        "measurement_period": cite("calendar_month", "subtracting from 100% the percentage of Downtime Minutes (as defined below) out of the total minutes in the relevant calendar month", "Appendix B · Calculation"),
        "timezone": cite("UTC", "All calendar months are measured in the UTC time zone.", "Appendix B · Calculation"),
        "downtime_definition": cite(
            {"kind": "request_error_rate", "interval_seconds": 60, "error_threshold_pct": "5",
             "description": "A Downtime Minute is a minute in which more than 5% of the customer's requests to covered experiences fail.",
             "covered_experiences": ["View Issue", "Create Issue", "Edit Issue", "View Board"]},
            "“Downtime Minute” occurs when the Error Rate in a given minute is greater than 5%.\n“Error Rate” means, over a given 1-minute period, "
            "the percentage of Customer’s requests to Covered Experiences resulting in an error out of Customer’s total requests to those Covered Experiences. "
            "[…] Jira (Premium and Enterprise)\nView Issue\nCreate Issue\nEdit Issue\nView Board "
            "[…] Covered Experiences include browser-based experiences only (not, e.g., integrations, API calls or mobile versions).",
            "Appendix A; Appendix B · Definitions"),
        "credit_tiers": cite(
            [{"lower": "99.0", "upper": "99.9", "credit_pct": "10"}, {"lower": "95.0", "upper": "99.0", "credit_pct": "25"},
             {"lower": "0", "upper": "95.0", "credit_pct": "50"}],
            "- Premium Plan Cloud Products -\nMonthly Uptime Percentage\nService Credit*\nLess than 99.9% but greater than or equal to 99.0%\n10%\n"
            "Less than 99.0% but greater than or equal to 95.0%\n25%\nLess than 95.0%\n50%",
            "Appendix B · Premium Plan Cloud Products"),
        "credit_basis": cite("affected_service", "Percentage of the monthly fees attributed to the affected Eligible Cloud Product. The percentage of monthly fees "
                             "attributable to an Eligible Cloud Product when purchased together with other Products under one SKU will be determined by Atlassian.",
                             "Appendix B · Notes"),
        "credit_cap": cite({"pct": "100", "basis": "receiving_invoice"},
                           "The aggregate maximum Service Credit applied to an invoice will not exceed 100% of the amount invoiced for the affected Cloud Product "
                           "in that invoice billing period (which, since Service Credits are applied to future payments, is not the month in which the affected Cloud Product was unavailable).",
                           "§2.2 Issuance"),
        "min_credit": {"value": None, "quote": None, "section": None, "absent": True,
                       "missing_reason": "Neither the SLA nor the compensation procedure states a minimum credit amount."},
        "exclusions": cite([{"kind": kind, "description": text, "machine_checkable": False} for kind, text in exclusions]
                           + [{"kind": "scheduled_maintenance", "description": "Routine scheduled maintenance under the Atlassian Maintenance Policy; subtract only confirmed windows", "machine_checkable": True}],
                           "Customer is not entitled to Service Credits if Customer is in breach of the Agreement (as defined below) or has not provisioned the relevant Cloud Product. "
                           "The Service Level Commitment does not include unavailability to the extent due to: (a) Customer’s use of the Cloud Products in a manner not authorized under the Agreement; "
                           "(b) force majeure events or other factors outside of Atlassian’s reasonable control, including internet access or related problems; "
                           "(c) Customer equipment, software, network connections or other infrastructure; (d) Customer Data or Customer Materials (or similar concepts defined in the Agreement); "
                           "(e) Third-Party Products; or (f) routine scheduled maintenance or reasonable emergency maintenance as stated in the Atlassian Maintenance Policy. "
                           "The Service Level Commitment does not apply to (i) sandbox instances or Free or Beta Products (or similar concepts in the Agreement) "
                           "or (ii) features excluded from the Service Level Commitment in the applicable Documentation.",
                           "§3 Exclusions"),
        "prerequisites": cite(["production_instance", "account_fully_paid", "support_ticket_during_incident", "authorized_requester"],
                              "provided that Customer’s account is fully paid up, without any overdue payments or disputes "
                              "[…] submit a technical support request during the incident "
                              "[…] You can only request SLA compensation for production instances of Premium and Enterprise apps. Trial and sandbox instances are ineligible for SLA compensation. "
                              "[…] You can submit a request for an app if you have one of the following roles: "
                              "[…] Billing contact or technical contact (original billing system), or Billing admin (new billing system). "
                              "[…] Site admin for the app or organization admin.",
                              "§2.2 Issuance; compensation procedure"),
        "claim_window": cite({"kind": "days_after_period", "value": 15, "timezone": None},
                             "within fifteen (15) days after the end of the calendar month in which the alleged failure occurred", "§2.1 Eligibility"),
        "claim_procedure": cite({"channel": "Atlassian support ticket → Compensation Requests → Cloud Premium SLA compensation request",
                                 "required_fields": ["other"], "subject_line": None},
                                "Customer must submit a ticket at https://support.atlassian.com with all fields fully and accurately completed "
                                "[…] From the dropdown What can we help you with?, select Compensation Requests. "
                                "[…] From Select a topic, select Cloud Premium SLA compensation request or Cloud Enterprise compensation request, depending on the plan of the app. "
                                "[…] If multiple incidents occur for one app during a given month, include all of them in one compensation ticket.",
                                "§2.1 Eligibility; compensation procedure"),
        "remedy_form": cite("service_credit", "Atlassian will apply the Service Credit, which will be calculated as described in Appendix B, against a future payment due from Customer "
                            "for the affected Cloud Product […] No refunds or cash value will be given for unused Service Credits.", "§2.2 Issuance"),
    }


def research_incident(id):
    return next(i for i in json.loads((ROOT / "docs/research/real-incidents.json").read_text())["incidents"] if i["id"] == id)


def real_october_incident() -> Incident:
    """The real October 21 sidebar incident: in-scope status was degraded performance, never an outage."""
    record = research_incident("1yh59bb4lw20")
    incident = normalize_incident({**record, "incident_updates": record["updates"], "shortlink": record["source_url"]}, set(JIRA_COVERED_COMPONENTS), "Jira")
    return incident.model_copy(update={"components": ["Viewing content"], "notes": "Real public incident. The vendor reported degraded performance, "
                                       "which is not counted as an outage; only the customer's request logs can show Downtime Minutes."})


def real_jira_incident() -> Incident:
    """The real September 3 incident, normalized from its historical component updates."""
    record = research_incident("ws0393rxcgyl")
    incident = normalize_incident({**record, "incident_updates": record["updates"], "shortlink": record["source_url"]}, set(JIRA_COVERED_COMPONENTS), "Jira")
    postmortem = record["postmortem_impact_window"]
    return incident.model_copy(update={
        "interval_basis": "Public status updates: covered components in major outage until monitoring began",
        "context_windows": [Window(label="Vendor postmortem impact window", intervals=[Interval(start=postmortem["start"], end=postmortem["end"])],
                                   source_url=record["source_url"], detail=postmortem["scope"])],
        "components": list(JIRA_COVERED_COMPONENTS.values()), "region": "US East",
        "notes": "Real public incident. Its windows show when the vendor reported impact; they are not this customer's Downtime Minutes. "
                 "The status-update window ends when monitoring began; the postmortem window is longer and includes intermittent failures."})


LOG_DIR = ROOT / "data/fixtures/logs"
SEPTEMBER = datetime(2026, 9, 1, tzinfo=timezone.utc)
COVERED = ["View Issue", "Create Issue", "Edit Issue", "View Board"]


def traffic(rng, at, base, minimum=0):
    """Requests per minute: busy during US business hours (13:00-23:00 UTC), quiet at night and on weekends."""
    level = (1.0 if 13 <= at.hour < 23 else 0.15) * (0.3 if at.weekday() >= 5 else 1.0)
    if at.weekday() == 6 and at.hour == 4:
        return 0  # the customer's own Sunday offline hour: no requests, so an Error Rate of 0%
    return max(minimum, int(rng.gauss(base * level, base * level * 0.15)))


def write_log(name, experiences, rows_for_minute, expected_minutes):
    """Write a simulated per-minute log and prove it yields the intended Downtime Minutes."""
    rng = random.Random(name)
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["minute", "experience", "requests", "errors"])
    for k in range(30 * 24 * 60):
        at = SEPTEMBER + timedelta(minutes=k)
        for experience, requests, errors in rows_for_minute(rng, at, experiences):
            writer.writerow([at.strftime("%Y-%m-%dT%H:%MZ"), experience, requests, errors])
    data = gzip.compress(buffer.getvalue().encode(), mtime=0)
    path = LOG_DIR / f"{name}.csv.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    analysis = downtime_from_samples(read_log(path), Decimal(5), COVERED)
    if analysis.downtime_minutes != expected_minutes:
        raise SystemExit(f"{name}: expected {expected_minutes} Downtime Minutes, got {analysis.downtime_minutes}")
    return {"path": str(path.relative_to(ROOT)), "sha256": hashlib.sha256(data).hexdigest()}, analysis


def in_window(at, start, end):
    return datetime.fromisoformat(start) <= at < datetime.fromisoformat(end)


def northstar_minute(rng, at, experiences):
    """Sept 3: errors above 5% from 15:04 to 16:22 and 16:30 to 16:48, intermittent failures around them."""
    down = in_window(at, "2026-09-03T15:04+00:00", "2026-09-03T16:22+00:00") or in_window(at, "2026-09-03T16:30+00:00", "2026-09-03T16:48+00:00")
    near = in_window(at, "2026-09-03T14:52+00:00", "2026-09-03T17:03+00:00") and not down
    rows = []
    for experience, base in experiences.items():
        requests = traffic(rng, at, base, minimum=20 if down else 0)
        if experience == "REST API":
            api_outage = in_window(at, "2026-09-03T15:00+00:00", "2026-09-03T16:50+00:00") or in_window(at, "2026-09-14T02:10+00:00", "2026-09-14T02:32+00:00")
            requests = max(requests, 5) if api_outage else requests
            errors = int(requests * (0.6 if at.day == 14 else 0.3)) if api_outage else 0
        elif at == datetime(2026, 9, 3, 16, 25, tzinfo=timezone.utc):
            requests, errors = (80, 4) if experience == "View Issue" else (20, 1)  # exactly 5.0%: not a Downtime Minute
        elif down:
            errors = max(1, round(requests * rng.uniform(0.12, 0.6)))
        elif near:
            errors = int(requests * 0.03)
        else:
            errors = 0
        rows.append([experience, requests, errors])
    covered = [r for r in rows if r[0] != "REST API"]
    if sum(r[1] for r in covered) >= 40 and not any(r[2] for r in covered) and rng.random() < 0.004:
        covered[0][2] = 1  # an isolated failed request, well under the threshold
    return rows


def cobalt_minute(rng, at, experiences):
    """Sept 19: every View Board request fails from 02:00 to 03:15, inside confirmed scheduled maintenance."""
    down = in_window(at, "2026-09-19T02:00+00:00", "2026-09-19T03:15+00:00")
    rows = []
    for experience, base in experiences.items():
        requests = traffic(rng, at, base, minimum=3 if down and experience == "View Board" else 0)
        rows.append([experience, requests, requests if down and experience == "View Board" else 0])
    return rows


def sample_minute(rng, at, experiences):
    """A simulated log for the upload demo: View Issue failing 15:10 to 16:12 on Sept 3, quiet otherwise."""
    down = in_window(at, "2026-09-03T15:10+00:00", "2026-09-03T16:12+00:00")
    requests = traffic(rng, at, experiences["View Issue"], minimum=20 if down else 0)
    return [["View Issue", requests, round(requests * rng.uniform(0.15, 0.5)) if down else 0]]


def customer_logs(id, log, analysis, related=None):
    return {"id": id, "title": "Customer request logs, September", "service": "Jira", "intervals": [],
            "interval_basis": "Computed by code from a simulated per-minute request log: minutes where more than 5% of covered-experience requests failed",
            "severity": "major_outage", "evidence_kind": "customer_metrics", "metric_verified": True, "related_incident": related, "log": log,
            "simulated": True, "notes": f"Simulated for this demo: {analysis.rows:,} log rows. A real claim would attach the customer's own request logs."}


def atlassian_case(id, summary, role, customer, invoice_fees, receiving_fees, prerequisites, incidents, provenance, *,
                   history_complete=True, exclusions_reviewed=True, maintenance=(), period="2026-09", as_of=AS_OF):
    source, refs = compose_source(["atlassian-sla", "atlassian-compensation"])
    short = customer.lower().split()[0]
    customer_id = f"{short}-demo"
    return Scenario.model_validate(dict(
        id=id, vendor="Atlassian", product="Jira", plan="Premium", color="#2360d6", initials="J", summary=summary, period=period, as_of=as_of,
        source_text=source, source_hash=source_hash(source), source_label="Atlassian SLA (effective Apr 30, 2026) and compensation procedure — verbatim excerpts",
        official_sla_url=SLA_URL, terms=atlassian_jira_premium_terms(), role=role, source_refs=refs,
        dispute_rule=cite({"business_days": 3}, "If you disagree with the amount awarded, you have 3 business days to request that we revisit the claim.",
                          "Compensation procedure · Receive compensation"),
        account=dict(customer_id=customer_id, display_name=f"{customer} (demo)", service="Jira", plan="Premium", prerequisites=prerequisites,
                     history_complete=history_complete, non_machine_exclusions_reviewed=exclusions_reviewed,
                     receiving_invoice_fees=receiving_fees, account_reference=f"DEMO-{short.upper()}-JIRA"),
        invoice=dict(id=f"DEMO-INV-{period}-{short.upper()}", customer_id=customer_id, vendor="Atlassian", service="Jira",
                     period=period, affected_fees=str(invoice_fees)),
        incidents=[i.model_dump(mode="json") if isinstance(i, Incident) else i for i in incidents], maintenance=list(maintenance),
        provenance=[{"item": "Agreement terms", "kind": "real", "detail": "Quoted from Atlassian's public SLA and compensation procedure; values reviewed against the quotes."}]
        + provenance))


def authored_boundary_case(id, vendor, product, plan, fees, color, initials, summary):
    """Authored agreement text that exercises an unsupported measurement. Not the vendor's terms."""
    period = "calendar_quarter" if vendor == "GitHub" else "calendar_month"
    metric = "visitor_ratio" if vendor == "Cloudflare" else "request_error_rate"
    tiers = [dict(lower="0", upper="95", credit_pct="50"), dict(lower="95", upper="99", credit_pct="25"), dict(lower="99", upper="99.9", credit_pct="10")]
    fields = {
        "vendor": (vendor, f"This authored demonstration agreement names {vendor} as the example vendor.", "Parties"),
        "covered_services": ([product], f"This demonstration covers the {product} service.", "§1 Coverage"),
        "plan": (plan, f"Only the {plan} plan is covered by this demonstration.", "§1 Coverage"),
        "uptime_commitment_pct": ("99.9", "The uptime commitment is 99.9%.", "§2 Commitment"),
        "measurement_period": (period, f"The measurement period is {period}.", "§2 Measurement"),
        "timezone": ("UTC", "All measurement periods use UTC.", "§2 Measurement"),
        "downtime_definition": (dict(kind=metric, interval_seconds=60, error_threshold_pct="5", description="Customer error rate exceeds 5% in a minute."),
                                f"The metric is {metric}; an error rate greater than 5% per 60-second interval counts as downtime. Visitor ratios, when required, need separate evidence.", "§3 Downtime"),
        "credit_tiers": (tiers, "\n".join(f"Uptime at least {t['lower']}% and less than {t['upper']}% earns a {t['credit_pct']}% credit." for t in tiers), "§4 Credit table"),
        "credit_basis": ("affected_service", "Credits are based only on the affected service's monthly fees.", "§4 Fee basis"),
        "credit_cap": (dict(pct="100", basis="affected_period_fees"), "The demonstration cap is 100% of the affected period's service fees.", "§4 Cap"),
        "min_credit": (dict(amount="1", inclusive=False), "A credit must be greater than $1.00 USD.", "§4 Minimum"),
        "exclusions": ([dict(kind="scheduled_maintenance", description="Confirmed scheduled maintenance", machine_checkable=True)], "Confirmed scheduled maintenance is excluded.", "§5 Exclusions"),
        "prerequisites": (["support_ticket_during_incident"], "A support ticket during the incident is required.", "§6 Requirements"),
        "claim_window": (dict(kind="days_after_period", value=15, timezone="UTC"), "Requests must arrive by the end of the fifteenth day after the period, in UTC.", "§6 Deadline"),
        "claim_procedure": (dict(channel="support portal", required_fields=["account_reference", "incident_times", "service", "period", "calculation"], subject_line=None),
                            "Submit through the support portal with the account reference, incident times, service, period, and calculation.", "§6 Procedure"),
        "remedy_form": ("service_credit", "The remedy is a future service credit, not a cash refund.", "§7 Remedy"),
    }
    terms = {key: cite(value, quote, section) for key, (value, quote, section) in fields.items()}
    source = "REFUND SHARK AUTHORED AGREEMENT\nSynthetic fixture for a capability boundary. Not a real vendor contract.\n\n" + "\n\n".join(f"{section}\n{quote}" for _, quote, section in fields.values())
    start = datetime(2026, 9, 12, 10, tzinfo=timezone.utc)
    incident = dict(id=f"DEMO-{id}", title=f"{product} service interruption (authored)", service=product,
                    intervals=[{"start": start.isoformat(), "end": (start + timedelta(minutes=90)).isoformat()}], severity="major_outage",
                    evidence_kind="synthetic", metric_verified=True, simulated=True, notes="Authored demo record. Not a real outage or customer record.")
    return Scenario.model_validate(dict(
        id=id, vendor=vendor, product=product, plan=plan, color=color, initials=initials, summary=summary, period="2026-09", as_of=AS_OF,
        source_text=source, source_hash=source_hash(source), source_label="Authored boundary agreement — not the vendor's terms", terms=terms,
        account=dict(customer_id="northstar-demo", display_name="Northstar Analytics (demo)", service=product, plan=plan,
                     prerequisites={"support_ticket_during_incident": True}, history_complete=True, non_machine_exclusions_reviewed=True,
                     account_reference="DEMO-NORTHSTAR-001"),
        invoice=dict(id=f"DEMO-INV-{id.upper()}", customer_id="northstar-demo", vendor=vendor, service=product, period="2026-09", affected_fees=str(fees)),
        incidents=[incident], role="capability_boundary",
        provenance=[{"item": "Agreement, incident, and invoice", "kind": "authored", "detail": "Written to exercise an unsupported calculation. Not the vendor's real terms or a real outage."}]))


def main():
    public = real_jira_incident()
    northstar_log, northstar = write_log("northstar-jira-2026-09", {"View Issue": 60, "Edit Issue": 20, "REST API": 30}, northstar_minute, 96)
    cobalt_log, cobalt = write_log("cobalt-jira-2026-09", {"View Board": 50, "Create Issue": 15}, cobalt_minute, 75)
    write_log("sample-upload-2026-09", {"View Issue": 40}, sample_minute, 62)  # the downloadable "measure your own logs" sample
    simulated_account = {"item": "Customer, invoice, receiving invoice, and support ticket", "kind": "simulated", "detail": "Demo records; no real customer or bill."}
    scenarios = [
        atlassian_case(
            "jira-sept-claim", "Request logs confirm downtime during the Sept 3 US East incident", "claim_candidate", "Northstar Analytics",
            9000, 9000, {"production_instance": True, "account_fully_paid": True, "support_ticket_during_incident": True},
            [public, customer_logs("DEMO-LOGS-NORTHSTAR-2026-09", northstar_log, northstar, public.id)],
            [{"item": "Sept 3 incident", "kind": "real", "detail": "Atlassian Statuspage incident ws0393rxcgyl with its historical updates and postmortem window."},
             {"item": "Customer request logs", "kind": "simulated", "detail": f"A simulated per-minute log for September ({northstar.rows:,} rows). Code computes "
              f"{northstar.downtime_minutes} Downtime Minutes under the SLA's definition; {sum(northstar.excluded_error_minutes.values())} error minutes on the REST API "
              "are excluded because API calls are not a covered experience."}, simulated_account]),
        atlassian_case(
            "jira-sept-missing-evidence", "Real Sept 3 incident; no customer request logs or ticket on file", "missing_evidence", "Harbor Logistics",
            4800, None, {"production_instance": True, "account_fully_paid": True, "support_ticket_during_incident": None}, [public],
            [{"item": "Sept 3 incident", "kind": "real", "detail": "Atlassian Statuspage incident ws0393rxcgyl with its historical updates and postmortem window."},
             {"item": "Customer evidence", "kind": "simulated", "detail": "Deliberately missing: no request logs, support ticket, or receiving invoice are supplied."},
             {"item": "Customer and invoice", "kind": "simulated", "detail": "Demo records; no real customer or bill."}],
            history_complete=False, exclusions_reviewed=False),
        atlassian_case(
            "jira-sept-maintenance", "Every logged error minute falls inside confirmed scheduled maintenance", "refusal", "Cobalt Studio",
            2400, 2400, {"production_instance": True, "account_fully_paid": True, "support_ticket_during_incident": True},
            [customer_logs("DEMO-LOGS-COBALT-2026-09", cobalt_log, cobalt)],
            [{"item": "Request logs and maintenance notice", "kind": "simulated", "detail": f"A simulated per-minute log ({cobalt.rows:,} rows): "
              f"{cobalt.downtime_minutes} Downtime Minutes, all inside a confirmed scheduled maintenance window."}, simulated_account],
            maintenance=[{"service": "Jira", "interval": {"start": "2026-09-19T01:45:00+00:00", "end": "2026-09-19T03:30:00+00:00"},
                          "exclusion_confirmed": True, "reference": "DEMO-MAINT-0919", "simulated": True}]),
        atlassian_case(
            "jira-oct-degraded", "Real Oct 21 sidebar degradation; October is still open", "missing_evidence", "Northstar Analytics",
            9000, None, {"production_instance": True, "account_fully_paid": True, "support_ticket_during_incident": None}, [real_october_incident()],
            [{"item": "Oct 21 incident", "kind": "real", "detail": "Atlassian Statuspage incident 1yh59bb4lw20; covered components were degraded, not in outage."},
             {"item": "Customer evidence", "kind": "simulated", "detail": "Not yet collected: no request logs or support ticket for October."},
             {"item": "Customer and invoice", "kind": "simulated", "detail": "Demo records; no real customer or bill."}],
            history_complete=False, exclusions_reviewed=False, period="2026-10", as_of=CURRENT_AS_OF),
        authored_boundary_case("github", "GitHub", "Enterprise Cloud", "Enterprise", 3200, "#252625", "G", "Quarterly measurement needs a supported calculation adapter"),
        authored_boundary_case("cloudflare", "Cloudflare", "Business", "Business", 2500, "#e4762f", "C", "Affected-visitor evidence is needed to calculate a credit"),
    ]
    jira = lambda plan, region, ref: {"vendor": "Atlassian", "product": "Jira", "plan": plan, "site_region": region, "account_reference": ref}
    directory = {"note": "Simulated customer directory: the vendor, product, plan, and site region that bills and card spend would show. Not real customers.",
                 "customers": [
                     {"customer_id": "northstar-demo", "display_name": "Northstar Analytics (demo)", "contact": "Finance operations (demo)",
                      "subscriptions": [jira("Premium", "US East", "DEMO-NORTHSTAR-JIRA"),
                                        {"vendor": "GitHub", "product": "Enterprise Cloud", "plan": "Enterprise", "account_reference": "DEMO-NORTHSTAR-001"},
                                        {"vendor": "Cloudflare", "product": "Business", "plan": "Business", "account_reference": "DEMO-NORTHSTAR-001"}]},
                     {"customer_id": "harbor-demo", "display_name": "Harbor Logistics (demo)", "contact": "Billing admin (demo)",
                      "subscriptions": [jira("Premium", "US East", "DEMO-HARBOR-JIRA")]},
                     {"customer_id": "cobalt-demo", "display_name": "Cobalt Studio (demo)", "contact": "Billing admin (demo)",
                      "subscriptions": [jira("Premium", "EU", "DEMO-COBALT-JIRA")]},
                     {"customer_id": "juniper-demo", "display_name": "Juniper Health (demo)", "contact": "IT procurement (demo)",
                      "subscriptions": [jira("Enterprise", "APAC", "DEMO-JUNIPER-JIRA")]},
                     {"customer_id": "pinecrest-demo", "display_name": "Pinecrest Labs (demo)", "contact": "Office manager (demo)",
                      "subscriptions": [jira("Standard", "US East", "DEMO-PINECREST-JIRA")]}]}
    for case in scenarios:
        if case.vendor == "Atlassian":
            case.account.prerequisites["authorized_requester"] = True
            case.provenance.append(Provenance(item="Requester authorization", kind="simulated", detail="Demo assumes an authorized billing administrator; no real person's permissions have been verified."))
    for customer in directory["customers"]:
        Customer.model_validate(customer)
    (ROOT / "data/fixtures/customers.json").write_text(json.dumps(directory, indent=2) + "\n")
    path = ROOT / "data/fixtures/scenarios.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([s.model_dump(mode="json", exclude_defaults=False) for s in scenarios], indent=2, ensure_ascii=False) + "\n")
    print(f"Wrote {len(scenarios)} demo scenarios: 4 cite Atlassian's real SLA, 2 are authored capability boundaries")


if __name__ == "__main__":
    main()
