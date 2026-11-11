"""Three simulated accounts through the same scan and evidence APIs, in a separate database."""
import threading
from pathlib import Path
from uuid import uuid4

from shark.documents import InvoiceFields, parse_pdf
from shark.pipeline import ROOT, Pipeline, load_scenarios
from shark.recovery import EvidenceInput, VendorSetup, add_evidence, save_vendor
from shark.scan import RecoveryScan, ReviewFacts
from shark.storage import Store, utcnow


class PortfolioReplay:
    def __init__(self, workspace, sample_text):
        self.workspace, self.sample_text = workspace, sample_text
        self.lock = threading.RLock()
        self.cached = None

    def context(self):
        state = self.workspace.record("portfolio_replay", "current")
        if not state:
            return None
        if self.cached and self.cached[0] == state["run_id"]:
            return self.cached[1:]
        store = Store(str(Path(self.workspace.path).parent / "portfolio-replay" / f"{state['run_id']}.sqlite3"))
        pipeline = Pipeline(store, scenarios=[])
        scan = RecoveryScan(store, pipeline)
        self.cached = (state["run_id"], store, pipeline, scan)
        return store, pipeline, scan

    def state(self):
        with self.lock:
            context = self.context()
            if context is None:
                return {"started": False, "items": [], "totals": {}, "history": [], "isolated": True}
            return context[2].state() | {"isolated": True, "evidence_arrived": bool(context[0].record("demo_step", "arrived"))}

    def start(self):
        """Open the demo without resetting progress on reload or duplicate requests."""
        with self.lock:
            state = self.state()
            return state if state["started"] else self.reset()

    def reset(self):
        with self.lock:
            self.workspace.save_record("portfolio_replay", {"id": "current", "run_id": uuid4().hex, "created_at": utcnow()})
            store, pipeline, scan = self.context()
            fixture = next(s for s in load_scenarios() if s.id == "jira-sept-claim")
            read = {"id": "portfolio-agreement", "text": fixture.source_text, "terms": fixture.terms.model_dump(mode="json"),
                "selection": {"service": "Jira", "plan": "Premium"}, "source": {"url": fixture.official_sla_url}, "source_kind": "excerpts", "model": "saved demo terms"}
            store.save_read(read["id"], read)
            for slug in ("northstar", "harbor", "cobalt"):
                docs = []
                for month in ("09", "10"):
                    path = ROOT / "data/fixtures/documents/portfolio" / f"{slug}-{month}.pdf"
                    raw = path.read_bytes()
                    doc = parse_pdf(raw, path.name, "invoice")
                    fields = InvoiceFields.model_validate({k: v["value"] for k, v in doc["suggestions"].items()} | {"reviewed": True})
                    doc.update(confirmed=fields.model_dump(mode="json"), confirmed_at=utcnow())
                    store.save_record("document", doc, raw)
                    docs.append(doc)
                inv = docs[0]["confirmed"]
                vendor = save_vendor(store, VendorSetup(**{k: inv[k] for k in ("customer", "vendor", "product", "plan", "account_reference", "billing_cycle")},
                    region="US East", owner_name="Demo finance", owner_email="finance@example.com", read_id=read["id"], invoice_id=docs[0]["id"], confirmed=True, simulated=True))
                add_evidence(store, vendor["id"], EvidenceInput(kind="logs", period="2026-09", label="Simulated monthly request log", csv=self.sample_text, simulated=True), "portfolio_demo", self.sample_text)
                if slug != "harbor":
                    self.ticket(store, vendor["id"])
            initial = scan.run(replay=True)
            for item in initial["items"]:
                if item["period"] != "2026-09":
                    continue
                maintenance = [{"service": "Jira", "interval": {"start": "2026-09-03T15:00:00Z", "end": "2026-09-03T17:30:00Z"},
                    "exclusion_confirmed": True, "reference": "Simulated confirmed maintenance", "simulated": True}] if "Cobalt" in item["customer"] else []
                scan.review(item["id"], ReviewFacts(expected_version=item["version"], reviewer="Simulated finance reviewer",
                    prerequisites={p: True for p in item["requirements"]}, exclusions_reviewed=True, maintenance=maintenance, confirmed=True))
            return self.state()

    def ticket(self, store, vendor_id):
        return add_evidence(store, vendor_id, EvidenceInput(kind="ticket", period="2026-09", label="Simulated support ticket",
            reference="DEMO-" + vendor_id[-8:], opened_at="2026-09-03T16:00:00Z", incident_start="2026-09-03T15:00:00Z",
            incident_end="2026-09-03T17:30:00Z", simulated=True), "portfolio_demo", self.sample_text)

    def arrive(self):
        with self.lock:
            context = self.context()
            if context is None:
                raise ValueError("Start the isolated portfolio replay first.")
            store, _, _ = context
            vendor = next(v for v in store.records("vendor") if "Harbor" in v["customer"])
            self.ticket(store, vendor["id"])
            store.save_record("demo_step", {"id": "arrived", "at": utcnow()})
            return self.state()
