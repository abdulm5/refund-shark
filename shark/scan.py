"""Evidence-driven recovery proposals. Scanning never approves or replaces a saved case."""
import hashlib
import json
import threading
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from shark.claims.drafter import draft
from shark.engine import evaluate
from shark.engine.metrics import apply_logs
from shark.models import Maintenance
from shark.monthly import SUBMITTED, identity, scenario_identity, save_monthly_case
from shark.pipeline import revision
from shark.recovery import VendorCase, case_from_vendor, same, vendor_checks
from shark.storage import utcnow


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


class ReviewFacts(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    expected_version: str
    reviewer: str = Field(min_length=1, max_length=120)
    prerequisites: dict[str, bool | None] = Field(default_factory=dict)
    exclusions_reviewed: bool = False
    maintenance: list[Maintenance] = Field(default_factory=list, max_length=30)
    confirmed: bool


class AcceptProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: str
    confirmed: bool


def action_for(check):
    name = check["name"].lower()
    if "contract" in name or "measurement period" in name or "credit table" in name:
        return "agreements"
    if "invoice" in name or "cap" in name:
        return "documents"
    if "support ticket" in name:
        return "vendors"
    return "review"


class RecoveryScan:
    def __init__(self, store, pipeline):
        self.store, self.pipeline = store, pipeline
        self.lock = threading.RLock()
        self.measured = {}

    def inputs(self):
        return {k: self.store.records(k) for k in ("vendor", "document", "evidence", "scan_facts", "monthly_case")} | {
            "reads": self.store.reads(), "claims": self.store.claims(),
            "cases": [s.model_dump(mode="json") for s in self.pipeline.scenarios.values() if s.role == "user_case"]}

    def state(self):
        state = self.store.record("recovery_scan", "current")
        return self.run() if state else {"id": "current", "started": False, "items": [], "history": [], "totals": {}}

    def run(self, force=False, replay=None):
        with self.lock, self.pipeline.lock:
            previous = self.store.record("recovery_scan", "current") or {}
            replay = previous.get("replay", False) if replay is None else replay
            inputs = self.inputs()
            if replay and any(not v["simulated"] for v in inputs["vendor"]):
                raise ValueError("Historical replay is available only when all configured vendors are simulated.")
            clock = datetime.fromisoformat("2026-10-08T12:00:00+00:00") if replay else datetime.now(timezone.utc).replace(second=0, microsecond=0)
            source_key = fingerprint([inputs, clock])
            if not force and previous.get("source_key") == source_key:
                return previous
            items = []
            for vendor in inputs["vendor"]:
                invoices = [d for d in inputs["document"] if d.get("confirmed") and all(
                    same(d["confirmed"].get(k), vendor.get(k)) for k in ("customer", "vendor", "product", "plan", "account_reference", "billing_cycle"))]
                if not invoices:
                    items.append(self.candidate(vendor, [], inputs, clock))
                # A receiving invoice can cap a claim without becoming a claim for an unfinished month.
                for period in sorted({d["confirmed"]["period"] for d in invoices if d["confirmed"]["period"] < clock.strftime("%Y-%m")}):
                    items.append(self.candidate(vendor, [d for d in invoices if d["confirmed"]["period"] == period], inputs, clock, invoices))
            # Two configured profiles for the same account/month cannot produce two recovery proposals.
            counts = {}
            for item in items:
                counts[item["id"]] = counts.get(item["id"], 0) + 1
            unique = {}
            for item in items:
                if counts[item["id"]] > 1:
                    item.update(status="missing", decision=None, draft=None, form=None,
                        blockers=[{"name": "Duplicate vendor setups", "status": "review", "detail": "Multiple vendor profiles match this account and month. Resolve the duplicate setups before accepting recovery.", "action": "vendors"}])
                    item["version"] = fingerprint(item)
                unique[item["id"]] = item
            items = sorted(unique.values(), key=lambda x: ({"ready": 0, "missing": 1, "ineligible": 2, "handled": 3}[x["status"]], x["period"] or "", x["customer"]))
            old = {x["id"]: x for x in previous.get("items", [])}
            changes = [{"customer": i["customer"], "period": i["period"], "before": old.get(i["id"], {}).get("status"), "after": i["status"]}
                       for i in items if old.get(i["id"], {}).get("version") != i["version"]]
            totals = {key: sum(i["status"] == key for i in items) for key in ("ready", "missing", "ineligible", "handled")}
            totals["supported"] = str(sum((Decimal(i["decision"]["amount"]) for i in items if i["status"] == "ready"), Decimal("0.00")))
            run = {"id": "current", "run_id": uuid4().hex, "started": True, "source_key": source_key, "replay": replay,
                "evaluated_at": clock.isoformat(), "updated_at": utcnow(), "items": items, "totals": totals,
                "changes": changes, "history": ([{"at": utcnow(), "changes": changes, "totals": totals}] + previous.get("history", []))[:12]}
            self.store.save_record("recovery_scan", run)
            return run

    def candidate(self, v, docs, inputs, clock, invoices=None):
        period = docs[0]["confirmed"]["period"] if docs else None
        key = "proposal-" + fingerprint(identity(v["customer"], v["vendor"], v["product"], v["account_reference"], period))[:24]
        item = {"id": key, "vendor_id": v["id"], "customer": v["customer"], "vendor": v["vendor"], "product": v["product"],
            "plan": v["plan"], "account": v["account_reference"], "owner": v["owner_name"] or "Unassigned", "period": period,
            "simulated": v["simulated"], "status": "missing", "decision": None, "draft": None, "blockers": [], "form": None,
            "case_id": None, "existing_revision": None, "claim_status": None, "read_id": v.get("read_id"), "facts": {}, "requirements": []}
        def finish():
            material = {k: val for k, val in item.items() if k not in {"version", "decision", "form"}}
            material["decision"] = {k: val for k, val in (item["decision"] or {}).items() if k != "evaluated_at"}
            material["form"] = {k: val for k, val in (item["form"] or {}).items() if k != "as_of"}
            item["version"] = fingerprint(material)
            return item
        def block(name, detail, action="review"):
            item["blockers"].append({"name": name, "detail": detail, "status": "review", "action": action})
        if len(docs) != 1:
            block("Invoice selection", "Multiple confirmed invoices cover this account and month. Resolve the billing ambiguity before scanning." if docs else "Confirm an invoice for this exact account, plan, and billing cycle.", "documents")
            return finish()
        doc = docs[0]
        item["invoice_id"] = doc["id"]
        for c in vendor_checks(self.store, v | {"invoice_id": doc["id"]}):
            if not c["done"]:
                block(c["label"], c["detail"], "vendors")
        read = self.store.get_read(v.get("read_id"))
        if not read or item["blockers"]:
            return finish()
        item["requirements"] = [p for p in read["terms"]["prerequisites"]["value"] or [] if "support_ticket" not in p]
        evidence = [e for e in inputs["evidence"] if e["vendor_id"] == v["id"] and e["period"] == period]
        logs = sorted(e["id"] for e in evidence if e["kind"] == "logs")
        tickets = sorted(e["id"] for e in evidence if e["kind"] == "ticket")
        basis = fingerprint([read, doc, sorted((e["id"], e["sha256"]) for e in evidence if e["kind"] == "logs")])
        item.update(evidence_count=len(evidence), log_count=len(logs), ticket_count=len(tickets), review_basis=basis)
        saved = self.store.record("scan_facts", key) or {}
        facts = saved if saved.get("review_basis") == basis else {}
        item["facts"] = facts
        matches = [s for s in self.pipeline.scenarios.values() if s.role == "user_case" and scenario_identity(s) == identity(v["customer"], v["vendor"], v["product"], v["account_reference"], period)]
        existing = next((s for s in matches if (self.store.get_claim(s.id) or {}).get("status") in SUBMITTED), matches[0] if matches else None)
        if existing:
            claim = self.store.get_claim(existing.id)
            item.update(case_id=existing.id, existing_revision=revision(existing, evaluate(existing)), claim_status=(claim or {}).get("status"))
            if claim and claim["status"] in SUBMITTED:
                item.update(status="handled", decision=evaluate(existing).model_dump(mode="json"))
                return finish()
        if len(matches) > 1:
            block("Duplicate cases", "Multiple older cases already cover this account/month. Review them before creating another draft.", "vendors")
        later = [d for d in invoices or [] if d["confirmed"]["period"] > period and d["confirmed"]["currency"] == doc["confirmed"]["currency"]]
        earliest = min((d["confirmed"]["period"] for d in later), default=None)
        receiving = [d for d in later if d["confirmed"]["period"] == earliest]
        if len(receiving) > 1:
            block("Receiving invoice", "Multiple invoices match the next billing period; the credit cap needs a reviewed invoice choice.", "documents")
        if not logs:
            block("Request logs", "Import this account's per-minute request log for the invoice month.", "vendors")
            return finish()
        form = VendorCase(invoice_id=doc["id"], evidence_ids=logs, ticket_ids=tickets,
            receiving_invoice_id=receiving[0]["id"] if len(receiving) == 1 else None, as_of=clock,
            prerequisites={p: facts.get("prerequisites", {}).get(p) for p in item["requirements"]}, exclusions_reviewed=facts.get("exclusions_reviewed", False),
            maintenance=facts.get("maintenance", []))
        item["form"] = form.model_dump(mode="json")
        try:
            s = self.scenario(v["id"], form, facts, key)
            d = evaluate(s)
            item["decision"] = d.model_dump(mode="json")
            item["blockers"].extend(c.model_dump(mode="json") | {"action": action_for(c.model_dump())} for c in d.checks if c.status != "pass")
            item["status"] = "missing" if item["blockers"] and d.outcome != "not_eligible" else "ineligible" if d.outcome == "not_eligible" else "ready"
            if d.outcome == "eligible" and not item["blockers"]:
                subject, body = draft(s, d)
                item["draft"] = {"subject": subject, "body": body}
                old = self.store.record("monthly_case", existing.id) if existing else None
                if old and old.get("scan_basis") == fingerprint([basis, tickets, form.receiving_invoice_id, facts]) and item["claim_status"] in {"draft", "approved"}:
                    item["status"] = "handled"
        except (ValueError, OSError) as error:
            item.update(status="missing", draft=None)
            block("Evidence review", str(error), "vendors")
        return finish()

    def scenario(self, vendor_id, form, facts, key):
        vendor = self.store.record("vendor", vendor_id)
        evidence = [self.store.record("evidence", id) for id in form.evidence_ids + form.ticket_ids]
        for e in evidence:
            raw = self.store.attachment(e["id"]) if e else None
            if not raw or hashlib.sha256(raw).hexdigest() != e["sha256"]:
                raise ValueError("A selected evidence attachment is missing or changed.")
        cache_key = fingerprint([vendor, self.store.get_read(vendor["read_id"]), evidence,
            self.store.record("document", form.invoice_id), self.store.record("document", form.receiving_invoice_id)])
        if cache_key not in self.measured:
            if len(self.measured) >= 32:
                self.measured.clear()
            self.measured[cache_key] = apply_logs(case_from_vendor(self.store, vendor_id, form))
        s = self.measured[cache_key].model_copy(deep=True)
        s.as_of = form.as_of
        for prerequisite in s.terms.prerequisites.value or []:
            if "support_ticket" not in prerequisite:
                s.account.prerequisites[prerequisite] = form.prerequisites.get(prerequisite)
        s.account.non_machine_exclusions_reviewed = form.exclusions_reviewed
        s.id = key
        for index, incident in enumerate(s.incidents):
            incident.id = f"{key}-logs-{index}"
        s.maintenance = [Maintenance.model_validate(m) for m in facts.get("maintenance", [])]
        return s

    def find(self, key):
        item = next((i for i in self.run(force=True)["items"] if i["id"] == key), None)
        if item is None:
            raise ValueError("This recovery proposal no longer exists. Run the scan again.")
        return item

    def review(self, key, form):
        with self.lock, self.pipeline.lock:
            item = self.find(key)
            if not form.confirmed or form.expected_version != item["version"]:
                raise ValueError("The proposal changed or was not confirmed. Reload and review its current evidence.")
            if not item.get("review_basis") or not item.get("log_count"):
                raise ValueError("Add the matching invoice and request logs before reviewing these facts.")
            if set(form.prerequisites) - set(item["requirements"]):
                raise ValueError("Only the listed non-ticket prerequisites can be confirmed here.")
            if any(m.service != item["product"] or not m.exclusion_confirmed for m in form.maintenance):
                raise ValueError("Maintenance must be confirmed for this product.")
            self.store.save_record("scan_facts", form.model_dump(mode="json", exclude={"expected_version", "confirmed"}) |
                {"id": key, "review_basis": item["review_basis"], "reviewed_at": utcnow()})
            return self.run(force=True)

    def accept(self, key, request):
        with self.lock, self.pipeline.lock:
            item = self.find(key)
            if not request.confirmed or item["version"] != request.expected_version:
                raise ValueError("The proposal changed or was not confirmed. Review the latest result before accepting.")
            if item["status"] != "ready":
                raise ValueError("Only a supported, review-ready proposal can become a draft.")
            form = VendorCase.model_validate(item["form"] | {"expected_revision": item["existing_revision"], "confirm_update": bool(item["case_id"])})
            result = save_monthly_case(self.store, self.pipeline, item["vendor_id"], form)
            s = self.pipeline.scenarios[result["id"]]
            claim = self.pipeline.create_claim(s.id)
            record = self.store.record("monthly_case", s.id)
            record["scan_basis"] = fingerprint([item["review_basis"], form.ticket_ids, form.receiving_invoice_id, item["facts"]])
            record["scan_review"] = item["facts"]
            self.store.save_record("monthly_case", record)
            self.store.event("scan", "Recovery proposal accepted", f"{item['customer']} · {item['period']} · draft requires approval")
            return {"case_id": s.id, "claim": claim, "scan": self.run(force=True)}
