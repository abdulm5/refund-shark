"""Vendor setup, durable evidence, and invoice-backed reconciliation."""
import hashlib
import json
import re
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from shark.cases import build_user_case
from shark.documents import MONTH
from shark.models import Customer, SLATerms, Subscription, Provenance, Maintenance
from shark.storage import Store, utcnow
from shark.whatif import measure_upload


def same(a, b):
    return bool(a and b) and str(a).strip().casefold() == str(b).strip().casefold()


class VendorSetup(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    customer: str = Field(min_length=1, max_length=120)
    vendor: str = Field(min_length=1, max_length=120)
    product: str = Field(default="", max_length=120)
    plan: str = Field(default="unknown", max_length=120)
    account_reference: str = Field(default="", max_length=120)
    region: str = Field(default="", max_length=120)
    owner_name: str = Field(default="", max_length=120)
    owner_email: str = Field(default="", max_length=254)
    billing_cycle: Literal["monthly", "annual"] = "monthly"
    read_id: str | None = None
    invoice_id: str | None = None
    confirmed: bool = False
    simulated: bool = False

    @model_validator(mode="after")
    def email(self):
        if self.owner_email and not re.fullmatch(r"[^\s@<>,;]+@[^\s@<>,;]+\.[^\s@<>,;]+", self.owner_email):
            raise ValueError("Enter one valid owner email address.")
        return self


def vendor_checks(store: Store, v: dict) -> list[dict]:
    read = store.get_read(v.get("read_id"))
    doc = store.record("document", v.get("invoice_id"))
    inv = (doc or {}).get("confirmed")
    identity = all(v.get(k) for k in ("customer", "vendor", "product", "account_reference", "region")) and v.get("plan", "unknown").lower() != "unknown" and v.get("confirmed")
    agreement = bool(read and same(read["selection"]["service"], v["product"]) and same(read["selection"]["plan"], v["plan"])
                     and same(read["terms"]["vendor"]["value"], v["vendor"]))
    invoice = bool(inv and all(same(inv[k], v[k]) for k in ("customer", "vendor", "product", "plan", "account_reference", "billing_cycle")))
    return [{"label": "Subscription confirmed", "done": bool(identity), "detail": "Confirm company, product, plan, account, and hosting region."},
            {"label": "Agreement linked", "done": agreement, "detail": "Read and link the agreement for this vendor, product, and plan."},
            {"label": "Invoice reviewed", "done": invoice, "detail": "Confirm an invoice that matches this account and subscription."},
            {"label": "Owner assigned", "done": bool(v.get("owner_name") and v.get("owner_email")), "detail": "Choose who receives actionable alerts."}]


def vendor_customer(v: dict) -> Customer:
    return Customer(customer_id=v["id"], display_name=v["customer"], contact=v["owner_name"] or "Unassigned",
                    source="setup", simulated=v["simulated"], subscriptions=[Subscription(vendor=v["vendor"],
                    product=v["product"] or None, plan=v["plan"] if v["confirmed"] else "unknown",
                    site_region=v["region"] if v["confirmed"] else None, account_reference=v["account_reference"] or None,
                    billing_cycle=v["billing_cycle"], evidence="Confirmed vendor setup" if v["confirmed"] else "Unconfirmed vendor setup")])


def save_vendor(store: Store, form: VendorSetup, id: str | None = None) -> dict:
    if id and not store.record("vendor", id):
        raise ValueError("That vendor setup no longer exists.")
    v = form.model_dump(mode="json") | {"id": id or "vendor-" + uuid4().hex[:16], "updated_at": utcnow()}
    read = store.get_read(v["read_id"]) if v["read_id"] else None
    if v["read_id"] and not read:
        raise ValueError("Read the agreement before linking it.")
    if read:
        for field, canonical in {"vendor": read["terms"]["vendor"]["value"], "product": read["selection"]["service"], "plan": read["selection"]["plan"]}.items():
            if same(v[field], canonical):
                v[field] = canonical
    doc = store.record("document", v["invoice_id"]) if v["invoice_id"] else None
    if v["invoice_id"] and (not doc or doc["kind"] != "invoice" or not doc["confirmed"]):
        raise ValueError("Review and confirm the invoice before linking it.")
    v["simulated"] = bool(v["simulated"] or (doc or {}).get("simulated"))
    store.save_record("vendor", v)
    store.event("setup", "Vendor setup saved", f"{v['customer']} · {v['vendor']} {v['product']}")
    return v | {"checklist": vendor_checks(store, v)}


class EvidenceInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    kind: Literal["logs", "ticket"]
    period: str = Field(pattern=MONTH)
    label: str = Field(min_length=1, max_length=160)
    csv: str | None = Field(default=None, max_length=8_000_000)
    reference: str | None = Field(default=None, max_length=120)
    url: str | None = Field(default=None, max_length=2000)
    opened_at: datetime | None = None
    incident_start: datetime | None = None
    incident_end: datetime | None = None
    simulated: bool = False

    @model_validator(mode="after")
    def shape(self):
        if self.kind == "logs" and not self.csv:
            raise ValueError("Provide the per-minute CSV log.")
        if self.kind == "ticket" and not (self.reference and self.opened_at):
            raise ValueError("A support ticket needs a reference and its opening time.")
        if self.url and not re.fullmatch(r"https://[^\s]+", self.url):
            raise ValueError("Use an https support-ticket link.")
        for value in (self.opened_at, self.incident_start, self.incident_end):
            if value and not value.tzinfo:
                raise ValueError("Evidence times must include a time zone.")
        if bool(self.incident_start) != bool(self.incident_end):
            raise ValueError("Provide both ends of the incident window, or leave both unknown.")
        if self.incident_start and self.incident_start >= self.incident_end:
            raise ValueError("The incident must end after it starts.")
        if self.incident_start and self.incident_start.strftime('%Y-%m') != self.period:
            raise ValueError("The ticket's incident must start in the selected claim month.")
        return self


def add_evidence(store: Store, vendor_id: str, form: EvidenceInput, source: str, sample_text: str) -> dict:
    v = store.record("vendor", vendor_id)
    if not v:
        raise ValueError("Save the vendor setup first.")
    data = form.model_dump(mode="json", exclude={"csv"})
    data.update(vendor_id=vendor_id, source=source, created_at=utcnow(), analysis=None, ticket_during_incident=None,
                simulated=form.simulated or v["simulated"] or bool(form.csv and form.csv.strip() == sample_text.strip()))
    content = (form.csv or json.dumps(data, sort_keys=True)).encode()
    if form.kind == "logs":
        read = store.get_read(v.get("read_id"))
        if not read:
            raise ValueError("Link an agreement before importing logs, so its downtime definition can be applied.")
        _, summary, _ = measure_upload(SLATerms.model_validate(read["terms"]), form.period, form.csv)
        data["analysis"] = summary.model_dump(mode="json")
        data["read_id"] = read["id"]
    elif form.incident_start:
        data["ticket_during_incident"] = form.incident_start <= form.opened_at < form.incident_end
    data["sha256"] = hashlib.sha256(content).hexdigest()
    # An identical webhook or file retry is idempotent; ticket identity excludes ingestion time.
    identity = [vendor_id, form.model_dump(mode="json"), data["sha256"] if form.kind == "logs" else None]
    data["id"] = "evidence-" + hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:24]
    store.save_record("evidence", data, content=content, only_new=True)
    return store.record("evidence", data["id"])


class VendorCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    invoice_id: str | None = None
    evidence_id: str | None = None
    evidence_ids: list[str] = Field(default_factory=list, max_length=100)
    ticket_id: str | None = None
    ticket_ids: list[str] = Field(default_factory=list, max_length=100)
    receiving_invoice_id: str | None = None
    as_of: datetime
    prerequisites: dict[str, bool | None] = {}
    exclusions_reviewed: bool = False
    expected_revision: str | None = None
    confirm_update: bool = False
    maintenance: list[Maintenance] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def aware(self):
        if not self.as_of.tzinfo:
            raise ValueError("The evaluation time needs a time zone.")
        return self


def case_from_vendor(store: Store, vendor_id: str, form: VendorCase):
    v = store.record("vendor", vendor_id)
    if v and form.invoice_id:
        v = v | {"invoice_id": form.invoice_id}
    if not v or not all(c["done"] for c in vendor_checks(store, v)):
        raise ValueError("Complete the vendor setup checklist first.")
    read = store.get_read(v["read_id"])
    doc = store.record("document", v["invoice_id"])
    inv = doc["confirmed"]
    from shark.monthly import merge_logs
    log_ids = sorted(set(([form.evidence_id] if form.evidence_id else []) + form.evidence_ids))
    logs = [store.record("evidence", id) for id in log_ids]
    if not logs or any(not log or log["kind"] != "logs" or log["vendor_id"] != vendor_id or log["period"] != inv["period"] for log in logs):
        raise ValueError("Choose this vendor's logs for the invoice month.")
    csv, merged = merge_logs(store, logs, inv["period"])
    analysis, _, _ = measure_upload(SLATerms.model_validate(read["terms"]), inv["period"], csv)
    if inv["currency"] != "USD":
        raise ValueError("The current credit engine supports USD invoices only.")
    prerequisites = dict(form.prerequisites)
    asked = read["terms"]["prerequisites"]["value"] or []
    ticket_ids = sorted(set(([form.ticket_id] if form.ticket_id else []) + form.ticket_ids))
    tickets = [store.record("evidence", id) for id in ticket_ids]
    if any(not t or t["kind"] != "ticket" or t["vendor_id"] != vendor_id or t["period"] != inv["period"] for t in tickets):
        raise ValueError("Choose this vendor's support tickets for the invoice month.")
    timely = [t for t in tickets if t["ticket_during_incident"] is True]
    windows = [(datetime.fromisoformat(t["incident_start"]), datetime.fromisoformat(t["incident_end"])) for t in timely]
    covered = bool(timely) and all(any(start <= span.start + timedelta(minutes=m) and span.start + timedelta(minutes=m+1) <= end for start, end in windows)
        for span in analysis.intervals for m in range(int((span.end - span.start).total_seconds() / 60)))
    ticket_status = True if covered else False if tickets and all(t["ticket_during_incident"] is False for t in tickets) else None
    for requirement in asked:
        if "support_ticket" in requirement:
            # The form cannot override missing evidence or a known timing failure.
            prerequisites[requirement] = ticket_status
    receiving, receiving_doc = None, None
    if form.receiving_invoice_id:
        receiving_doc = store.record("document", form.receiving_invoice_id)
        other = (receiving_doc or {}).get("confirmed")
        if not other or not all(same(inv[k], other[k]) for k in ("customer", "vendor", "product", "account_reference", "currency")) or other["period"] <= inv["period"]:
            raise ValueError("The receiving invoice must be a later invoice for the same account, product, and currency.")
        receiving = other["fees"]
    form_values = SimpleNamespace(read_id=read["id"], customer_name=v["customer"], account_reference=v["account_reference"], period=inv["period"],
        as_of=form.as_of, billing_cycle=inv["billing_cycle"], fees=inv["fees"], receiving_invoice_fees=receiving,
        prerequisites=prerequisites, exclusions_reviewed=form.exclusions_reviewed, csv=csv,
        demo=bool(v["simulated"] or doc["simulated"] or any(e["simulated"] for e in logs + tickets) or (receiving_doc or {}).get("simulated")))
    s = build_user_case(read, form_values, Path(store.path).parent / "uploads")
    if any(m.service != v["product"] or not m.exclusion_confirmed for m in form.maintenance):
        raise ValueError("Maintenance must be confirmed for this product.")
    s.maintenance = [m.model_copy(deep=True) for m in form.maintenance]
    s.account.customer_id = vendor_id
    s.invoice.customer_id = vendor_id
    s.invoice.id = inv["invoice_number"]
    s.provenance.append(Provenance(item="Confirmed invoice PDF", kind="simulated" if doc["simulated"] else "user",
        detail=f"{doc['filename']}; SHA-256 {doc['sha256']}. Fields confirmed by the workspace user; not verified against the vendor billing system."))
    s.provenance.append(Provenance(item="Monthly consolidation", kind="simulated" if s.simulated else "user",
        detail=f"{merged['records']} logs; {merged['rows']} unique minute/experience rows; {merged['duplicate_rows_removed']} identical overlapping rows removed. Conflicting counts are refused."))
    if read.get("review"):
        r = read["review"]
        s.provenance.append(Provenance(item="Approved agreement version", kind="user", detail=f"{read['id']}, version {r['version']}, approved by {r['reviewer']} at {r['approved_at']}. {r['reason']}"))
    for ticket in tickets:
        s.provenance.append(Provenance(item="Support ticket", kind="simulated" if ticket["simulated"] else "user",
            detail=f"{ticket['reference']}, opened {ticket['opened_at']}; timing checked against the supplied incident window. {ticket.get('url') or ''}"))
    return s


def reconcile(store: Store, pipeline, case_id: str, document_id: str) -> dict:
    if case_id not in pipeline.scenarios:
        raise ValueError("Choose an existing claim.")
    d, key = pipeline.run(case_id)
    claim = store.get_claim(case_id)
    if not claim or claim["status"] not in {"sent", "short_paid", "disputed", "credited"} or claim["decision_hash"] != key or d.outcome != "eligible":
        raise ValueError("Reconciliation requires a supported claim marked sent (submission remains simulated).")
    doc = store.record("document", document_id)
    inv = (doc or {}).get("confirmed")
    if not inv:
        raise ValueError("Review and confirm the later invoice first.")
    s = pipeline.scenarios[case_id]
    comparisons = {"Customer": same(inv["customer"], s.account.display_name), "Vendor": same(inv["vendor"], s.vendor),
        "Product": same(inv["product"], s.product), "Plan": same(inv["plan"], s.plan), "Account": same(inv["account_reference"], s.account.account_reference),
        "Currency": inv["currency"] == claim["currency"], "Later invoice": inv["period"] > s.period}
    observed = Decimal(inv["credit_amount"])
    if observed:
        comparisons.update({"Claim reference": inv["claim_reference"] == claim["id"], "Claim month": inv["credit_period"] == s.period})
    expected = Decimal(claim["award_amount"] if claim.get("award_amount") is not None else claim["amount"])
    outcome = ("mismatch" if not all(comparisons.values()) else "missing" if not observed else "partial" if observed < expected else "excess" if observed > expected else "matched")
    payload = {"id": "recon-" + hashlib.sha256(f"{case_id}|{document_id}|{key}|{expected}".encode()).hexdigest()[:24],
        "case_id": case_id, "claim_reference": claim["id"], "document_id": document_id, "filename": doc["filename"], "document_sha256": doc["sha256"],
        "customer": s.account.display_name, "vendor": s.vendor, "product": s.product, "period": s.period, "currency": claim["currency"],
        "observed_currency": inv["currency"], "expected": str(expected), "observed": str(observed),
        "difference": str(expected - observed) if all(comparisons.values()) else None, "outcome": outcome,
        "checks": [{"label": k, "done": v} for k, v in comparisons.items()], "created_at": utcnow(),
        "simulated": bool(doc["simulated"] or s.simulated), "submission_simulated": True,
        "basis": "User-confirmed invoice; no vendor or bank account was queried. Claim state is unchanged."}
    store.save_record("reconciliation", payload)
    store.event("reconcile", f"Invoice credit: {outcome}", f"{claim['id']} · expected {expected} · observed {observed} {inv['currency']}")
    return payload
