"""Local recovery workspace routes. External evidence ingestion requires a bearer token."""
import hmac
import os
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, ValidationError
from starlette.concurrency import run_in_threadpool

from shark.documents import InvoiceFields, MAX_PDF_BYTES, parse_pdf, public_document
from shark.notifications import configuration, deliver, email_message, sync_outbox
from shark.recovery import EvidenceInput, VendorCase, VendorSetup, add_evidence, case_from_vendor, reconcile, save_vendor, vendor_checks, vendor_customer
from shark.storage import utcnow
from shark.monthly import save_monthly_case


class ReconcileInput(BaseModel):
    case_id: str
    document_id: str


class DeliveryInput(BaseModel):
    channel: Literal["email", "slack"]
    confirmed: Literal[True]


def routes(store, pipeline, watcher, sample_text: str):
    router = APIRouter(prefix="/api")

    def required(kind, id):
        value = store.record(kind, id)
        if not value:
            raise HTTPException(404, f"That {kind} is not saved in this workspace.")
        return value

    def checked(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ValidationError as error:
            first = error.errors(include_input=False)[0]
            raise HTTPException(422, first["msg"]) from error
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @router.get("/documents")
    def documents():
        return {"documents": [public_document(d) for d in store.records("document")]}

    @router.get("/documents/samples/{name}")
    def sample(name: Literal["invoice", "credit", "contract"]):
        path = Path(__file__).resolve().parents[1] / "data/fixtures/documents" / f"sample-{name}.pdf"
        return Response(path.read_bytes(), media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="sample-{name}.pdf"'})

    @router.post("/documents")
    async def upload(request: Request, kind: Literal["contract", "invoice"], filename: str = "document.pdf"):
        content = bytearray()
        async for chunk in request.stream():
            content.extend(chunk)
            if len(content) > MAX_PDF_BYTES:
                raise HTTPException(413, "PDFs must be 10 MB or smaller.")
        doc = await run_in_threadpool(checked, parse_pdf, bytes(content), filename, kind)
        store.save_record("document", doc, bytes(content), only_new=True)
        return public_document(required("document", doc["id"]), detail=True)

    @router.get("/documents/{id}")
    def document(id: str):
        return public_document(required("document", id), detail=True)

    @router.get("/documents/{id}/file")
    def file(id: str):
        doc = required("document", id)
        return Response(store.attachment(id), media_type="application/pdf", headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(doc['filename'])}",
            "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox"})

    @router.post("/documents/{id}/confirm")
    def confirm(id: str, body: InvoiceFields):
        doc = required("document", id)
        if doc["kind"] != "invoice":
            raise HTTPException(422, "Only invoice fields can be confirmed here.")
        if doc["confirmed"]:
            raise HTTPException(409, "This invoice is already confirmed. Upload a corrected document to preserve existing evidence.")
        doc.update(confirmed=body.model_dump(mode="json"), confirmed_at=utcnow())
        store.save_record("document", doc)
        store.event("document", "Invoice fields confirmed", doc["filename"])
        return public_document(doc, detail=True)

    @router.get("/agreements/reads")
    def reads():
        return {"reads": [{"id": r["id"], "selection": r["selection"], "source": r["source"], "vendor": r["terms"]["vendor"]["value"],
                           "prerequisites": r["terms"]["prerequisites"]["value"] or [], "review": r.get("review"),
                           "covered_experiences": (r["terms"]["downtime_definition"]["value"] or {}).get("covered_experiences", [])} for r in store.reads()]}

    @router.get("/vendors")
    def vendors():
        return {"vendors": [v | {"checklist": vendor_checks(store, v)} for v in store.records("vendor")]}

    def save(body, id=None):
        v = checked(save_vendor, store, body, id)
        watcher.add_customers([vendor_customer(v)])
        return v

    @router.post("/vendors")
    def create_vendor(body: VendorSetup):
        return save(body)

    @router.put("/vendors/{id}")
    def update_vendor(id: str, body: VendorSetup):
        required("vendor", id)
        return save(body, id)

    @router.get("/vendors/{id}/evidence")
    def evidence(id: str):
        required("vendor", id)
        return {"evidence": [e for e in store.records("evidence") if e["vendor_id"] == id],
                "integration_configured": bool(os.getenv("SHARK_EVIDENCE_TOKEN"))}

    @router.post("/vendors/{id}/evidence")
    def upload_evidence(id: str, body: EvidenceInput):
        return checked(add_evidence, store, id, body, "uploaded", sample_text)

    @router.post("/integrations/evidence/{id}")
    async def ingest_evidence(id: str, request: Request):
        token = os.getenv("SHARK_EVIDENCE_TOKEN", "")
        supplied = request.headers.get("authorization", "")
        if not token or not hmac.compare_digest(supplied, f"Bearer {token}"):
            raise HTTPException(401, "Configure and supply the evidence integration bearer token.")
        content = bytearray()
        async for chunk in request.stream():
            content.extend(chunk)
            if len(content) > 9_000_000:
                raise HTTPException(413, "Evidence requests must be under 9 MB.")
        body = checked(EvidenceInput.model_validate_json, bytes(content))
        return checked(add_evidence, store, id, body, "integration", sample_text)

    @router.post("/vendors/{id}/cases")
    def create_case(id: str, body: VendorCase):
        return checked(save_monthly_case, store, pipeline, id, body)

    @router.get("/notifications")
    def notifications():
        return {"notifications": sync_outbox(store, pipeline), "configuration": configuration()}

    @router.get("/notifications/{id}/email")
    def draft_email(id: str):
        item = required("notification", id)
        return Response(email_message(item).as_bytes(), media_type="message/rfc822",
                        headers={"Content-Disposition": f'attachment; filename="{id}.eml"'})

    @router.post("/notifications/{id}/send")
    def send(id: str, body: DeliveryInput):
        return checked(deliver, store, id, body.channel, pipeline)

    @router.get("/reconciliations")
    def reconciliations():
        return {"reconciliations": store.records("reconciliation")}

    @router.post("/reconciliations")
    def reconcile_invoice(body: ReconcileInput):
        return checked(reconcile, store, pipeline, body.case_id, body.document_id)

    return router
