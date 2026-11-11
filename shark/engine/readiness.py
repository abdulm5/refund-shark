"""Before any incident or invoice: could the engine price a credit under these terms at all?"""
from shark.extract.citations import validate_rules
from shark.models import SLATerms

from .decision import OPTIONAL_TERMS


def readiness(terms: SLATerms, status: dict[str, str]) -> dict:
    checks = []

    def check(name: str, ok: bool, detail: str, field: str | None = None):
        checks.append({"name": name, "status": "pass" if ok else "review", "detail": detail, "field": field})

    unsupported = [n for n, s in status.items() if s in {"missing", "unverified"} or (s == "absent" and n not in OPTIONAL_TERMS)]
    check("Quoted evidence", not unsupported, "Every term is quoted from the source or confirmed absent." if not unsupported
          else f"Missing, unverified, or required but absent: {', '.join(unsupported)}.")
    errors = validate_rules(terms) if terms.credit_tiers.value is not None else []
    check("Contract rules", not errors, "; ".join(errors) or "Tiers do not overlap; the commitment is a valid percentage.", "credit_tiers")
    period, zone = terms.measurement_period.value, terms.timezone.value
    check("Measurement period", period == "calendar_month" and zone == "UTC",
          "UTC calendar month." if period == "calendar_month" and zone == "UTC" else f"{period or 'Unknown'} in {zone or 'an unstated time zone'} needs another adapter.",
          "measurement_period")
    metric = terms.downtime_definition.value
    if metric is None:
        check("Downtime metric", False, "No downtime definition was extracted.", "downtime_definition")
    elif metric.kind == "request_error_rate":
        supported = metric.interval_seconds == 60 and metric.error_threshold_pct is not None
        check("Downtime metric", supported, f"Per-minute error rate above {metric.error_threshold_pct}%: needs the customer's request logs." if supported
              else "This request-error rule needs another adapter.", "downtime_definition")
    else:
        check("Downtime metric", metric.kind == "availability_minutes", "Availability minutes: needs the customer's outage records."
              if metric.kind == "availability_minutes" else f"A {metric.kind.replace('_', ' ')} metric cannot be priced from outage minutes.", "downtime_definition")
    tiers = terms.credit_tiers.value or []
    check("Credit table", bool(tiers), f"{len(tiers)} tier{'' if len(tiers) == 1 else 's'}." if tiers else "No credit tiers were extracted.", "credit_tiers")
    check("Fee basis", terms.credit_basis.value == "affected_service", "Credits are a share of the affected product's fees."
          if terms.credit_basis.value == "affected_service" else f"A {terms.credit_basis.value or 'missing'} fee basis needs another adapter.", "credit_basis")
    cap = terms.credit_cap.value
    cap_ok = cap is None and terms.credit_cap.absent or cap is not None and cap.basis in {"affected_period_fees", "receiving_invoice"}
    check("Credit cap", cap_ok, "No cap is stated." if cap is None and terms.credit_cap.absent else
          (f"{cap.pct}% of the {'receiving invoice' if cap.basis == 'receiving_invoice' else 'affected period fees'}." if cap_ok
           else "The cap basis needs another adapter."), "credit_cap")
    window = terms.claim_window.value
    check("Claim deadline", window is not None and window.kind == "days_after_period",
          ("" if window is None else f"{window.value} days after the period" + (f", {window.timezone}." if window.timezone
           else "; no time zone, so the earliest and latest readings are shown.")) if window and window.kind == "days_after_period"
          else "This deadline rule needs another adapter.", "claim_window")
    remedy = terms.remedy_form.value
    check("Remedy", remedy in {"service_credit", "refund"}, {"service_credit": "A future service credit, not cash.", "refund": "A refund."}.get(remedy, "Unclear remedy."),
          "remedy_form")
    return {"can_price": all(c["status"] == "pass" for c in checks), "checks": checks}
