"""A replayable walkthrough in its own database; reset never deletes workspace evidence."""
import threading
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from shark.documents import InvoiceFields, parse_pdf
from shark.incident_actions import TicketCapture, capture_ticket, action_panel
from shark.incidents.watcher import Watcher
from shark.pipeline import Pipeline, ROOT, load_scenarios
from shark.recovery import VendorSetup, VendorCase, EvidenceInput, add_evidence, case_from_vendor, save_vendor, vendor_customer, reconcile
from shark.storage import Store, utcnow

STEPS = ["Detect the outage", "Capture the support ticket", "Measure the monthly evidence", "Approve the claim", "Match the invoice credit"]


class GuidedDemo:
    def __init__(self, workspace, sample_text):
        self.workspace, self.sample_text = workspace, sample_text
        self.lock = threading.RLock()

    def state(self):
        return self.workspace.record("guided_demo", "current") or {"id": "current", "run_id": None, "step": 0, "steps": STEPS,
            "simulated": True, "message": "Start an isolated walkthrough. Your workspace stays as it is."}

    def context(self, state):
        store = Store(str(Path(self.workspace.path).parent / "guided-demo" / f"{state['run_id']}.sqlite3"))
        pipeline = Pipeline(store, scenarios=[])
        fixture = next(s for s in load_scenarios() if s.id == "jira-sept-claim")
        watcher = Watcher(store, [fixture], [vendor_customer(v) for v in store.records("vendor")], get=lambda *a, **k: {"incidents": []})
        return store, pipeline, watcher, fixture

    def reset(self):
        with self.lock:
            state = {"id": "current", "run_id": uuid4().hex, "step": 0, "steps": STEPS, "simulated": True,
                     "started_at": utcnow(), "message": "Ready. This walkthrough has its own sample records; no external services will be contacted."}
            self.workspace.save_record("guided_demo", state)
            return state

    def advance(self, expected_step):
        with self.lock:
            state = self.state()
            if not state["run_id"]:
                state = self.reset()
            if state["step"] != expected_step:
                raise ValueError("This walkthrough advanced in another tab. Reload it before continuing.")
            if state["step"] >= len(STEPS):
                return state
            store, pipeline, watcher, fixture = self.context(state)
            if state["step"] == 0:
                raw = (ROOT / "data/fixtures/documents/sample-invoice.pdf").read_bytes()
                doc = parse_pdf(raw, "sample-invoice.pdf", "invoice")
                fields = InvoiceFields.model_validate({k: v["value"] for k, v in doc["suggestions"].items()} | {"reviewed": True})
                doc.update(confirmed=fields.model_dump(mode="json"), confirmed_at=utcnow())
                store.save_record("document", doc, raw)
                read = {"id": "demo-agreement", "text": fixture.source_text, "terms": fixture.terms.model_dump(mode="json"),
                    "selection": {"service": "Jira", "plan": "Premium"}, "source": {"url": fixture.official_sla_url}, "source_kind": "excerpts", "model": "saved demo terms"}
                store.save_read(read["id"], read)
                inv = doc["confirmed"]
                vendor = save_vendor(store, VendorSetup(**{k: inv[k] for k in ("customer", "vendor", "product", "plan", "account_reference", "billing_cycle")},
                    region="US East", owner_name="Demo finance", owner_email="finance@example.com", read_id=read["id"], invoice_id=doc["id"], confirmed=True, simulated=True))
                watcher.add_customers([vendor_customer(vendor)])
                watcher.start_drill(datetime.fromisoformat("2026-09-03T15:00:00+00:00"))
                alert = next(a for a in store.alerts() if a["customer_id"] == vendor["id"])
                state.update(vendor_id=vendor["id"], alert_id=alert["id"], action=action_panel(store, watcher, alert["id"]),
                    message="A simulated Jira outage matches Northstar's Premium subscription in US East. Demo finance owns the next action.")
            elif state["step"] == 1:
                state["action"] = capture_ticket(store, watcher, state["alert_id"], TicketCapture(reference="DEMO-SUPPORT-001",
                    opened_at="2026-09-03T16:00:00Z", incident_start="2026-09-03T15:00:00Z", confirmed=True), self.sample_text)
                state["message"] = "The simulated support ticket is recorded at 16:00 UTC. The impact end remains unknown until the incident is resolved."
            elif state["step"] == 2:
                watcher.end_drill(datetime.fromisoformat("2026-09-03T17:30:00+00:00"))
                state["action"] = capture_ticket(store, watcher, state["alert_id"], TicketCapture(reference="DEMO-SUPPORT-001",
                    opened_at="2026-09-03T16:00:00Z", incident_start="2026-09-03T15:00:00Z", incident_end="2026-09-03T17:30:00Z", confirmed=True), self.sample_text)
                log = add_evidence(store, state["vendor_id"], EvidenceInput(kind="logs", period="2026-09", label="Simulated September request log", csv=self.sample_text, simulated=True), "guided_demo", self.sample_text)
                raw = (ROOT / "data/fixtures/documents/sample-credit.pdf").read_bytes()
                doc = parse_pdf(raw, "sample-credit.pdf", "invoice")
                fields = InvoiceFields.model_validate({k: v["value"] for k, v in doc["suggestions"].items()} | {"reviewed": True})
                doc.update(confirmed=fields.model_dump(mode="json"), confirmed_at=utcnow())
                store.save_record("document", doc, raw)
                read = store.get_read("demo-agreement")
                scenario = case_from_vendor(store, state["vendor_id"], VendorCase(evidence_id=log["id"],
                    ticket_id=state["action"]["capture"]["evidence_id"], receiving_invoice_id=doc["id"], as_of="2026-10-08T12:00:00Z",
                    prerequisites={p: True for p in read["terms"]["prerequisites"]["value"]}, exclusions_reviewed=True))
                # Stable replay identity agrees with the reference printed on the bundled invoice.
                scenario.id = "jira-sept-claim"
                pipeline.add_user_case(scenario)
                case = pipeline.detail(scenario.id)
                claim = pipeline.create_claim(scenario.id)
                state.update(case_id=scenario.id, credit_document_id=doc["id"], decision=case["decision"], claim=claim,
                    message="Code measures 62 qualifying minutes and supports a $900 service credit at the October 8 replay clock.")
            elif state["step"] == 3:
                claim = pipeline.create_claim(state["case_id"])
                _, revision = pipeline.run(state["case_id"])
                pipeline.transition(state["case_id"], "approve", revision)
                pipeline.transition(state["case_id"], "mark_sent", revision)
                state.update(claim=store.get_claim(state["case_id"]), message="The demo claim is approved and marked sent. This only advances the simulated workflow; nothing was submitted to Atlassian.")
            else:
                claim = store.get_claim(state["case_id"])
                result = reconcile(store, pipeline, state["case_id"], state["credit_document_id"])
                state.update(claim=claim, reconciliation=result, message="The reviewed October sample invoice contains the matching $900 credit. No bank or vendor account was queried.")
            state["step"] += 1
            state["updated_at"] = utcnow()
            self.workspace.save_record("guided_demo", state)
            return state
