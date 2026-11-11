import csv
import gzip
import hashlib
import io
import json
import os
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Literal

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field, model_validator

from eval.run_eval import GOLD_MANIFEST, load_source
from shark.engine.readiness import readiness
from shark.extract.citations import source_hash, term_status
from shark.extract.fetch import fetch_public, save_snapshot
from shark.extract.provider import extract
from shark.incidents.live import http_get, live_status
from shark.incidents.matching import load_directory, match_customers
from shark.incidents.watcher import Watcher
from shark.models import Customer, SLATerms
from shark.spend.ramp import API_BASES, SAMPLE, SCOPES, FixtureRamp, HttpRamp, import_business
from shark.pipeline import Pipeline, ROOT
from shark.storage import Store
from shark.calendar import ics_event
from shark.cases import build_user_case
from shark.whatif import what_if_from_log
from shark.documents import citation_pages
from shark.recovery import vendor_customer
from api.recovery import routes as recovery_routes
from api.workflows import routes as workflow_routes
from shark.monitoring import Monitoring, monitoring_interval

SAMPLE_LOG = ROOT / "data/fixtures/logs/sample-upload-2026-09.csv.gz"
SAMPLE_TEXT = gzip.decompress(SAMPLE_LOG.read_bytes()).decode()

load_dotenv(ROOT / ".env")


class DraftRequest(BaseModel):
    tone: Literal["polite", "firm"] = "polite"


class ReadRequest(BaseModel):
    """Exactly one source: a public https link, pasted text, or a catalog agreement with saved model runs."""
    url: str | None = Field(default=None, max_length=2000)
    text: str | None = Field(default=None, max_length=250_000)
    catalog_id: str | None = Field(default=None, max_length=80)
    document_id: str | None = Field(default=None, max_length=80)
    service: str | None = Field(default=None, min_length=1, max_length=120)
    plan: str | None = Field(default=None, min_length=1, max_length=120)

    @model_validator(mode="after")
    def one_source(self):
        if sum(bool(v) for v in (self.url, self.text, self.catalog_id, self.document_id)) != 1:
            raise ValueError("Provide exactly one of url, text, catalog_id, or document_id")
        if not self.catalog_id and not (self.service and self.plan):
            raise ValueError("Name the service and plan to extract")
        return self


def saved_run(catalog_id: str, scenarios: dict | None = None) -> tuple[dict, str | None]:
    """The first saved model run for a catalog agreement, re-verified against its source.

    The source is the full page when its snapshot is cached here; otherwise the committed verbatim excerpts
    the demo cases cite, when this agreement has them (a fresh clone has no private cache). Also returns
    that source text, or None when neither is available."""
    manifest = json.loads(GOLD_MANIFEST.read_text())
    doc = next((d for d in manifest["documents"] if d["id"] == catalog_id and d.get("extraction_paths")), None)
    if doc is None:
        raise HTTPException(404, "No saved model run for that agreement")
    run = json.loads((GOLD_MANIFEST.parent / doc["extraction_paths"][0]).read_text())
    terms = SLATerms.model_validate(run["terms"])
    try:
        source, against = load_source(doc, GOLD_MANIFEST.parent), "full_page"
    except (OSError, ValueError):
        cited = [s for i in doc.get("scenario_ids") or [] if (s := (scenarios or {}).get(i)) is not None and s.source_refs]
        source, against = (cited[0].source_text, "excerpts") if cited else (None, None)
    status, verified_now = (term_status(terms, source), True) if source is not None else (run.get("term_status", {}), False)
    return {"mode": "saved_run", "model": run["model"], "served_by": run.get("served_by"), "extracted_at": run.get("extracted_at"),
            "verified_now": verified_now, "verified_against": against, "source": {"url": doc["url"], "title": doc.get("title"), "sha256": run["source_hash"]},
            "selection": doc["selection"], "runs_saved": len(doc["extraction_paths"]), "terms": run["terms"], "term_status": status,
            "readiness": readiness(terms, status), "usage": run.get("usage", {})}, source


class CaseForm(BaseModel):
    """A case started from an agreement read here: the user's own customer, invoice, prerequisites, and request log."""
    read_id: str = Field(max_length=80)
    customer_name: str = Field(min_length=1, max_length=120)
    account_reference: str | None = Field(default=None, max_length=120)
    period: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    as_of: datetime
    billing_cycle: Literal["monthly", "annual"] = "monthly"
    fees: Decimal = Field(gt=0, max_digits=14, decimal_places=2)  # the product's fee for the period (the annual fee when billed annually)
    receiving_invoice_fees: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)
    prerequisites: dict[str, bool | None] = {}
    exclusions_reviewed: bool = False
    csv: str = Field(min_length=1, max_length=8_000_000)
    demo: bool = False  # built on the bundled sample log: every customer record is labeled simulated

    @model_validator(mode="after")
    def aware(self):
        if self.as_of.tzinfo is None:
            raise ValueError("as_of needs a time zone")
        return self


class LogUpload(BaseModel):
    csv: str = Field(max_length=8_000_000)


class ImportRequest(BaseModel):
    source: Literal["sample", "api"]  # api reads RAMP_ACCESS_TOKEN from the server's environment; tokens never travel in requests


class DrillRequest(BaseModel):
    action: Literal["start", "end"]


class ActionRequest(BaseModel):
    action: Literal["approve", "reject", "mark_sent", "record_award", "send_dispute", "accept_award", "match_credit"]
    revision: str
    amount: Decimal | None = Field(default=None, ge=0)  # the (simulated) award, or a revised credit


def create_app(db_path: str | None = None, status_get=http_get, watch_interval: int | None = None):
    # The status watcher is off unless asked for (make demo sets SHARK_WATCH_INTERVAL), so tests never poll the network.
    interval = int(os.environ.get("SHARK_WATCH_INTERVAL", "0")) if watch_interval is None else watch_interval
    store = Store(db_path or os.environ.get("SHARK_DB", str(ROOT / ".cache/refund-shark.sqlite3")))
    pipeline = Pipeline(store)
    imported = []
    for payload in store.imported_customers():
        try:
            imported.append(Customer.model_validate_json(payload))
        except ValueError as error:
            store.event("spend", "Imported customer not loaded", str(error)[:200])
    watcher = Watcher(store, list(pipeline.scenarios.values()), customers=load_directory() + imported +
                      [vendor_customer(v) for v in store.records("vendor")], get=status_get)
    stop = threading.Event()
    monitoring = Monitoring(store, SAMPLE_TEXT)

    @asynccontextmanager
    async def lifespan(_app):
        thread = None
        monitor_thread = None
        if interval > 0:
            def loop():
                while True:
                    try:
                        watcher.poll()
                    except Exception as error:  # a bad poll must not stop the watcher; record it instead
                        store.event("watch", "Status check failed", str(error)[:200])
                    if stop.wait(interval):
                        break
            thread = threading.Thread(target=loop, name="status-watcher", daemon=True)
            thread.start()
        if monitoring_interval() > 0:
            def monitor_loop():
                while not stop.is_set():
                    try:
                        monitoring.tick()
                    except Exception:
                        store.event("monitoring", "Monitoring check failed", "Review the Datadog connection and retry manually.")
                    if stop.wait(monitoring_interval()):
                        break
            monitor_thread = threading.Thread(target=monitor_loop, name="metric-monitor", daemon=True)
            monitor_thread.start()
        yield
        stop.set()
        if thread:
            thread.join(timeout=5)
        if monitor_thread:
            monitor_thread.join(timeout=5)

    app = FastAPI(title="Refund Shark", version="0.1.0", description="Local demo. All financial records and claim actions are simulated.",
                  lifespan=lifespan)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request, exc: RequestValidationError):
        # One readable sentence; never echo the rejected input back (an upload can be megabytes).
        first = exc.errors()[0] if exc.errors() else {}
        where = ".".join(str(part) for part in first.get("loc", ()) if part != "body")
        message = first.get("msg", "Invalid request")
        return JSONResponse(status_code=422, content={"detail": f"{where}: {message}" if where else message})
    app.state.pipeline = pipeline

    @app.get("/api/health")
    def health():
        return {"status": "ok", "mode": "demo", "clock": "per-scenario replay; current-time outcomes are read-only"}

    @app.get("/api/opportunities")
    def opportunities():
        return {"items": pipeline.list(), "mode": "demo"}

    @app.get("/api/opportunities/{scenario_id}")
    def detail(scenario_id: str):
        if scenario_id not in pipeline.scenarios:
            raise HTTPException(404, "Opportunity not found")
        return pipeline.detail(scenario_id)

    @app.post("/api/opportunities/{scenario_id}/draft")
    def create_draft(scenario_id: str, body: DraftRequest):
        try:
            return pipeline.create_claim(scenario_id, body.tone)
        except KeyError:
            raise HTTPException(404, "Opportunity not found")
        except ValueError as e:
            raise HTTPException(409, str(e))

    @app.post("/api/opportunities/{scenario_id}/actions")
    def action(scenario_id: str, body: ActionRequest):
        try:
            return pipeline.transition(scenario_id, body.action, body.revision, amount=body.amount)
        except KeyError:
            raise HTTPException(404, "Opportunity not found")
        except ValueError as e:
            raise HTTPException(409, str(e))

    @app.post("/api/scan")
    def scan():
        rows = pipeline.list()
        pipeline.store.event("scan", "Demo agreements checked", f"{len(rows)} scenarios re-evaluated against saved evidence.")
        return {"checked": len(rows), "eligible": sum(r["decision"]["outcome"] == "eligible" for r in rows), "mode": "offline_replay"}

    @app.get("/api/reminders")
    def reminders():
        return {"reminders": pipeline.reminders(), "impact": pipeline.impact()}

    live_cache: dict = {}

    @app.get("/api/status/live")
    def status_live(preview: bool = False):
        # Vendor status pages are public; poll them at most once a minute.
        at, value = live_cache.get(preview, (0.0, None))
        if value is None or time.monotonic() - at > 60:
            value = live_status(list(pipeline.scenarios.values()), status_get, preview=preview, customers=watcher.customers)
            live_cache[preview] = (time.monotonic(), value)
        return value

    @app.get("/api/opportunities/{scenario_id}/deadline.ics")
    def deadline_ics(scenario_id: str):
        if scenario_id not in pipeline.scenarios:
            raise HTTPException(404, "Opportunity not found")
        s = pipeline.scenarios[scenario_id]
        d, _ = pipeline.run(scenario_id)
        if d.deadline is None:
            raise HTTPException(409, "This case has no computed claim deadline")
        reading = (" This is the earliest reading: the clause names no time zone; the latest reading is "
                   f"{d.deadline_latest:%b %d, %Y %H:%M} UTC.") if d.deadline_latest and d.deadline_latest != d.deadline else ""
        body = ics_event(f"{scenario_id}-deadline@refund-shark", d.deadline, f"File service credit claim: {s.vendor} {s.product} ({s.account.display_name or s.account.customer_id})",
                         f"{d.reason}. Period {s.period}.{reading} Refund Shark is a demo; submission is simulated.")
        return Response(body, media_type="text/calendar", headers={"Content-Disposition": f'attachment; filename="{scenario_id}-deadline.ics"'})

    @app.get("/api/logs/sample.csv")
    def sample_log():
        return Response(gzip.decompress(SAMPLE_LOG.read_bytes()).decode(), media_type="text/csv",
                        headers={"Content-Disposition": 'attachment; filename="sample-request-log-2026-09.csv"'})

    @app.post("/api/opportunities/{scenario_id}/logs/analyze")
    def analyze_log(scenario_id: str, body: LogUpload):
        if scenario_id not in pipeline.scenarios:
            raise HTTPException(404, "Opportunity not found")
        try:
            return what_if_from_log(pipeline.scenarios[scenario_id], body.csv)
        except ValueError as error:
            raise HTTPException(422, str(error)) from None

    @app.get("/api/alerts")
    def alerts():
        incidents = []
        for row in store.watched_list():
            payload = json.loads(row["payload"])
            incidents.append({"key": row["key"], "vendor": row["vendor"], "product": row["product"], "name": row["name"], "open": bool(row["open"]),
                              "simulated": bool(row["simulated"]), "first_seen": row["first_seen"], "resolved_at": row["resolved_at"],
                              "started_at": payload.get("started_at"), "shortlink": payload.get("shortlink"),
                              "matches": match_customers(payload, row["vendor"], row["product"], watcher.customers, watcher.scenarios)})
        return {"alerts": store.alerts(), "unread": store.unread_alerts(), "incidents": incidents,
                "watch": {"interval_seconds": interval, "last_poll": store.get_state("last_poll"), "drill_active": bool(watcher.drills),
                          "feeds": [{"vendor": v, "product": p, "host": h} for v, p, h in watcher.feeds]}}

    @app.post("/api/alerts/{alert_id}/read")
    def read_alert(alert_id: int):
        store.mark_alert_read(alert_id)
        return {"unread": store.unread_alerts()}

    @app.post("/api/watch/poll")
    def poll_now():
        return watcher.poll()

    @app.post("/api/watch/drill")
    def drill(body: DrillRequest):
        return watcher.start_drill() if body.action == "start" else watcher.end_drill()

    @app.post("/api/spend/ramp/import")
    def import_ramp(body: ImportRequest):
        if body.source == "sample":
            source, simulated = FixtureRamp(SAMPLE), True
        else:
            token = os.environ.get("RAMP_ACCESS_TOKEN")
            if not token:
                raise HTTPException(409, f"Set RAMP_ACCESS_TOKEN (scopes: {SCOPES}) on the server to import from Ramp.")
            environment = os.environ.get("RAMP_ENVIRONMENT", "production")
            if environment not in API_BASES:
                raise HTTPException(409, "RAMP_ENVIRONMENT must be production or sandbox.")
            source, simulated = HttpRamp(token, API_BASES[environment]), False
        try:
            customer, report = import_business(source, simulated)
        except httpx.HTTPStatusError as error:
            raise HTTPException(502, f"Ramp answered {error.response.status_code}; check the token and its scopes ({SCOPES}).")
        except (httpx.HTTPError, ValueError, KeyError) as error:
            raise HTTPException(502, f"Couldn't read from Ramp: {type(error).__name__}.")
        store.save_imported(customer.customer_id, customer.model_dump_json())
        created = watcher.add_customers([customer])
        store.event("spend", "Customer imported from Ramp" + (" (sample)" if simulated else ""),
                    f"{customer.display_name}: {', '.join(report['watched']) or 'no watched vendors'}" + (f"; {created} alert{'' if created == 1 else 's'} for open incidents." if created else "."))
        return {"customer": customer.model_dump(mode="json"), "report": report, "new_alerts": created}

    @app.get("/api/spend/customers")
    def spend_customers():
        return {"customers": [c.model_dump(mode="json") for c in watcher.customers if c.source == "ramp"],
                "ramp_configured": bool(os.environ.get("RAMP_ACCESS_TOKEN")), "scopes": SCOPES}

    @app.delete("/api/spend/customers/{customer_id}")
    def delete_spend_customer(customer_id: str):
        if not store.delete_imported(customer_id):
            raise HTTPException(404, "No imported customer has that id")
        watcher.remove_customer(customer_id)
        return {"deleted": customer_id}

    @app.get("/api/activity")
    def activity():
        return {"events": pipeline.store.events()}

    @app.get("/api/agreements/saved")
    def saved_agreements():
        manifest = json.loads(GOLD_MANIFEST.read_text())
        return {"agreements": [{"id": d["id"], "title": d.get("title"), "url": d["url"], "split": d["split"], "selection": d.get("selection"),
                                "runs": len(d.get("extraction_paths") or [])} for d in manifest["documents"] if d.get("extraction_paths")]}

    def remember(response: dict, text: str | None, source_kind: str) -> dict:
        """Keep the read (with its source text) so a case can start from it; a read without its text can't."""
        if text is None:
            return {**response, "read_id": None, "case_prerequisites": [],
                    "case_unavailable_reason": "This agreement's source page isn't cached here, so its quotes can't be re-checked. Re-read it from its link to start a case."}
        read_id = "read-" + hashlib.sha256(json.dumps([source_hash(text), response["selection"], response["terms"]], sort_keys=True).encode()).hexdigest()[:12]
        pipeline.store.save_read(read_id, {"id": read_id, "text": text, "terms": response["terms"], "source": response["source"],
                                           "selection": response["selection"], "source_kind": source_kind, "model": response.get("model")})
        return {**response, "read_id": read_id, "case_prerequisites": response["terms"]["prerequisites"]["value"] or [], "case_unavailable_reason": None}

    @app.post("/api/agreements/read")
    def read_agreement(body: ReadRequest):
        if body.catalog_id:
            response, text = saved_run(body.catalog_id, pipeline.scenarios)
            return remember(response, text, "excerpts" if response["verified_against"] == "excerpts" else "fetched")
        url, document = None, None
        if body.url:
            try:
                content, content_type, url = fetch_public(body.url)
                meta = save_snapshot(content, content_type, url, ROOT / ".cache/sources", "pasted-" + hashlib.sha256(url.encode()).hexdigest()[:12],
                                     datetime.now(timezone.utc))
            except ValueError as error:
                raise HTTPException(400, str(error))
            except httpx.HTTPError:
                raise HTTPException(502, "Couldn't download that page.")
            text = Path(meta["text_path"]).read_text()
        elif body.document_id:
            document = store.record("document", body.document_id)
            if not document or document["kind"] != "contract":
                raise HTTPException(404, "Upload a contract PDF before reading it.")
            text = document["text"]
        else:
            text = body.text
        try:
            result = extract(text, body.service, body.plan, ROOT / ".cache/extractions")
        except ValueError as error:
            raise HTTPException(409 if "ANTHROPIC_API_KEY" in str(error) else 502, str(error))
        terms = SLATerms.model_validate(result["terms"])
        status = term_status(terms, text)
        response = remember({"mode": result["mode"], "model": result.get("model"), "served_by": result.get("served_by"), "extracted_at": result.get("extracted_at"),
                         "verified_now": True, "verified_against": "full_page", "source": {"url": url, "chars": len(text), "sha256": source_hash(text)},
                         "selection": {"service": body.service, "plan": body.plan}, "terms": result["terms"], "term_status": status,
                         "readiness": readiness(terms, status), "usage": result.get("usage", {})}, text, "pdf" if document else "fetched" if url else "pasted")
        if document:
            response["source"].update(title=document["filename"], document_id=document["id"])
            response["page_citations"] = citation_pages(result["terms"], document["pages"])
            saved = store.get_read(response["read_id"])
            saved.update(source=response["source"], page_citations=response["page_citations"])
            store.save_read(response["read_id"], saved)
        return response

    @app.post("/api/cases")
    def start_case(body: CaseForm):
        read = pipeline.store.get_read(body.read_id)
        if read is None:
            raise HTTPException(404, "That agreement read isn't saved here; read the agreement again.")
        if body.csv.strip() == SAMPLE_TEXT.strip():  # however it was uploaded, the bundled sample is simulated
            body = body.model_copy(update={"demo": True})
        try:
            s = build_user_case(read, body, Path(store.path).parent / "uploads")
        except ValueError as error:
            raise HTTPException(422, str(error))
        try:
            s = pipeline.add_user_case(s)
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
        d, key = pipeline.run(s.id)
        return {"id": s.id, "decision": d.model_dump(mode="json"), "revision": key}

    @app.delete("/api/cases/{case_id}")
    def delete_case(case_id: str):
        try:
            if not pipeline.delete_user_case(case_id):
                raise HTTPException(404, "No case you started has that id")
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
        return {"deleted": case_id}

    @app.get("/api/sources")
    def sources():
        return json.loads((ROOT / "docs/research/sla-catalog.json").read_text())

    @app.get("/api/incidents")
    def incidents():
        return json.loads((ROOT / "docs/research/real-incidents.json").read_text())

    @app.get("/api/settings")
    def settings():
        return {**pipeline.store.preferences(), "model_configured": bool(os.getenv("ANTHROPIC_API_KEY")), "model": os.getenv("SHARK_MODEL", "claude-sonnet-5-5"),
                "invoice_source": "Demo JSON", "sending_enabled": False, "evaluation_status": "24 saved runs on 8 agreements; not scored for accuracy (no human-reviewed labels)"}

    @app.patch("/api/settings")
    def update_settings(body: DraftRequest):
        pipeline.store.set_preference("tone", body.tone)
        return settings()

    @app.get("/api/export")
    def export():
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["Customer", "Vendor", "Product", "Plan", "Period", "Replay clock", "Decision", "Supported credit USD", "Provisional credit USD",
                         "Provisional basis", "Decision at current time", "Claim status", "Data"])
        for r in pipeline.list():
            d = r["decision"]
            # Provisional estimates only exist for undecided cases; a supported row has nothing provisional.
            provisional = d["outcome"] == "needs_review" and d["estimated_amount"] is not None
            writer.writerow([r["customer"], r["vendor"], r["product"], r["plan"], r["period"], r["as_of"], d["outcome"], d["amount"],
                             d["estimated_amount"] if provisional else "", d["estimate_basis"] if provisional else "", r["current"]["outcome"],
                             r["claim"]["status"] if r["claim"] else "Not drafted",
                             "SIMULATED DEMO customer and invoice" if r["role"] != "user_case" else
                             "Simulated: built on the bundled sample log" if r["simulated"] else "Entered in this workspace"])
        return Response(buf.getvalue(), media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="refund-shark-demo.csv"'})

    from api.final_workflows import routes as final_routes
    app.include_router(final_routes(store, pipeline, SAMPLE_TEXT))
    app.include_router(recovery_routes(store, pipeline, watcher, SAMPLE_TEXT))
    app.include_router(workflow_routes(store, pipeline, watcher, monitoring, SAMPLE_TEXT))
    return app


app = create_app()
