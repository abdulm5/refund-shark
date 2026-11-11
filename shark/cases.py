"""Cases a user starts from an agreement read in this workspace: their own customer, invoice, and request log."""
import gzip
import hashlib
import re
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from shark.extract.citations import source_hash
from shark.models import Cited, DisputeRule, Incident, LogRef, Provenance, Scenario, SLATerms
from shark.pipeline import ROOT
from shark.whatif import measure_upload

# A dispute window is read only when the agreement states it in these words; anything else leaves it unset.
DISPUTE = re.compile(r"[^.\n]*\byou have (\d+) business days to request that we revisit\b[^.\n]*\.?", re.IGNORECASE)


def dispute_rule(text: str) -> Cited[DisputeRule] | None:
    match = DISPUTE.search(text)
    if not match or not 0 < int(match.group(1)) <= 366:  # an implausible window is not read (and could not be counted)
        return None
    return Cited[DisputeRule](value=DisputeRule(business_days=int(match.group(1))), quote=match.group(0).strip(), section="Agreement text")


def stored_path(path: Path) -> str:
    """Repository-relative when the upload lives in the repository, so a moved checkout still finds it."""
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path.resolve())


def build_user_case(read: dict, form, uploads: Path) -> Scenario:
    """Validate a case form against the agreement's own terms and build a scenario; the engine decides the rest."""
    terms = SLATerms.model_validate(read["terms"])
    asked = terms.prerequisites.value or []
    unknown = set(form.prerequisites) - set(asked)
    if unknown:
        raise ValueError(f"This agreement's prerequisites are: {', '.join(asked) or 'none'}. Unknown: {', '.join(sorted(unknown))}.")
    _, summary, basis = measure_upload(terms, form.period, form.csv)  # refuses a log the agreement's metric can't measure

    data = gzip.compress(form.csv.encode(), mtime=0)
    digest = hashlib.sha256(data).hexdigest()
    uploads.mkdir(parents=True, exist_ok=True)
    log_path = uploads / f"{digest}.csv.gz"
    log_path.write_bytes(data)

    service, plan = read["selection"]["service"], read["selection"]["plan"]
    vendor = terms.vendor.value or service
    case_id = "case-" + hashlib.sha256(f"{read['id']}|{form.customer_name}|{form.period}|{digest}|{datetime.now().isoformat()}".encode()).hexdigest()[:10]
    customer_id = re.sub(r"[^a-z0-9]+", "-", form.customer_name.lower()).strip("-") or "customer"
    annual = form.billing_cycle == "annual"
    fees = Decimal(form.fees)
    source_kind = "user" if read["source_kind"] in {"pasted", "pdf"} else "real"
    where = (("uploaded as a PDF" if read["source_kind"] == "pdf" else "pasted into this workspace") if source_kind == "user" else
             f"in the committed verbatim excerpts of {read['source'].get('url')}" if read["source_kind"] == "excerpts" else f"retrieved from {read['source'].get('url')}")
    demo = form.demo
    incident = Incident(id=f"LOGS-{case_id}", title=f"Request logs, {form.period}", service=service, intervals=[], interval_basis=basis,
                        severity="major_outage", evidence_kind="customer_metrics", metric_verified=True,
                        log=LogRef(path=stored_path(log_path), sha256=digest), simulated=demo,
                        notes=f"{'The bundled sample log (simulated)' if demo else 'Uploaded in this workspace'}: {summary.rows:,} log rows.")
    return Scenario.model_validate({
        "id": case_id, "vendor": vendor, "product": service, "plan": plan, "color": "#5b6472", "initials": (vendor[:1] or "?").upper(),
        "summary": f"{form.customer_name}: uploaded request logs for {form.period}", "period": form.period, "as_of": form.as_of,
        "source_text": read["text"], "source_hash": source_hash(read["text"]),
        "source_label": f"Agreement {where}; terms read by {read.get('model') or 'the model'} and quotes checked by code",
        "official_sla_url": read["source"].get("url"), "terms": terms,
        "account": {"customer_id": customer_id, "plan": plan, "service": service, "prerequisites": form.prerequisites,
                    "history_complete": False, "non_machine_exclusions_reviewed": form.exclusions_reviewed,
                    "receiving_invoice_fees": form.receiving_invoice_fees, "account_reference": form.account_reference,
                    "billing_cycle": form.billing_cycle, "display_name": form.customer_name},
        "invoice": {"id": f"INV-{case_id}", "customer_id": customer_id, "vendor": vendor, "service": service, "period": form.period,
                    "affected_fees": (fees / 12).quantize(Decimal("0.01")) if annual else fees, "annual_fees": fees if annual else None, "simulated": demo},
        "incidents": [incident], "simulated": demo, "role": "user_case", "dispute_rule": dispute_rule(read["text"]),
        "provenance": [
            Provenance(item="Agreement terms", kind=source_kind, detail=f"Agreement text {where}. The model's quotes are checked word for word against it."),
            Provenance(item="Customer request logs", kind="simulated" if demo else "user",
                       detail=f"{'The bundled sample log' if demo else 'Uploaded'}: {summary.rows:,} rows; code measures Downtime Minutes under the agreement's definition."),
            Provenance(item="Customer, invoice, and prerequisites", kind="simulated" if demo else "user",
                       detail="Demo values entered with the sample log." if demo else "Entered in the case form; not checked against a billing system."),
        ],
    })
