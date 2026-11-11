"""Monthly account identity, lossless log consolidation, and explicit case revisions."""
import csv
import hashlib
import io
import json
from datetime import timezone

from shark.engine.intervals import month_bounds
from shark.engine.metrics import parse_log
from shark.models import Provenance
from shark.storage import utcnow

SUBMITTED = {"sent", "short_paid", "disputed", "credited"}


def identity(customer, vendor, product, account, period):
    return [str(x or "").strip().casefold() for x in (customer, vendor, product, account, period)]


def scenario_identity(s):
    return identity(s.account.display_name, s.vendor, s.product, s.account.account_reference, s.period)


def vendor_identity(store, vendor_id, invoice_id=None):
    v = store.record("vendor", vendor_id)
    doc = store.record("document", invoice_id or (v or {}).get("invoice_id"))
    if not v or not (doc or {}).get("confirmed"):
        return None
    return identity(v["customer"], v["vendor"], v["product"], v["account_reference"], doc["confirmed"]["period"])


def existing_month(store, pipeline, vendor_id, invoice_id=None):
    key = vendor_identity(store, vendor_id, invoice_id)
    cases = [s for s in pipeline.scenarios.values() if s.role == "user_case" and key and scenario_identity(s) == key]
    if not cases:
        return None
    # Previously submitted legacy cases take precedence over drafts.
    cases.sort(key=lambda s: (store.get_claim(s.id) or {}).get("status") in SUBMITTED, reverse=True)
    s = cases[0]
    _, revision = pipeline.run(s.id)
    return {"id": s.id, "period": s.period, "revision": revision, "claim_status": (store.get_claim(s.id) or {}).get("status"),
            "record": store.record("monthly_case", s.id), "legacy_duplicates": len(cases) - 1}


def merge_logs(store, logs, period):
    start, end = month_bounds(period)
    unique, duplicates, source_bytes = {}, 0, 0
    for log in logs:
        raw = store.attachment(log["id"])
        source_bytes += len(raw or b"")
        if source_bytes > 32_000_000:
            raise ValueError("Selected logs exceed 32 MB. Consolidate the source exports before importing them.")
        if not raw or hashlib.sha256(raw).hexdigest() != log["sha256"]:
            raise ValueError("A saved log is missing or its hash changed. Import it again.")
        for sample in parse_log(raw.decode()):
            if not start <= sample.minute < end:
                continue
            key = (sample.minute.astimezone(timezone.utc), sample.experience)
            counts = (sample.requests, sample.errors)
            if key in unique:
                if unique[key] != counts:
                    raise ValueError(f"Conflicting counts at {key[0].isoformat()} for {key[1]}. Select one authoritative log for that interval.")
                duplicates += 1
            unique[key] = counts
    if not unique:
        raise ValueError("The selected logs contain no data in the invoice month.")
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["minute", "experience", "requests", "errors"])
    for (at, experience), counts in sorted(unique.items()):
        writer.writerow([at.isoformat(), experience, *counts])
    if out.tell() > 16_000_000:
        raise ValueError("The combined monthly log exceeds 16 MB. Consolidate the export first.")
    return out.getvalue(), {"records": len(logs), "rows": len(unique), "duplicate_rows_removed": duplicates}


def save_monthly_case(store, pipeline, vendor_id, form):
    from shark.recovery import case_from_vendor
    with pipeline.lock:
        old = existing_month(store, pipeline, vendor_id, form.invoice_id)
        v = store.record("vendor", vendor_id)
        fingerprint = hashlib.sha256(json.dumps({"vendor": v, "form": form.model_dump(mode="json", exclude={"expected_revision", "confirm_update"})}, sort_keys=True).encode()).hexdigest()
        if old and old["record"] and old["record"]["fingerprint"] == fingerprint:
            return pipeline.detail(old["id"]) | {"id": old["id"], "reused": True, "monthly": old["record"]}
        if old:
            if old["claim_status"] in SUBMITTED:
                raise ValueError(f"Monthly claim {old['id']} was already submitted. Open it to follow up; a second claim is blocked.")
            if not form.confirm_update or form.expected_revision != old["revision"]:
                raise ValueError(f"A monthly case already exists ({old['id']}). Review it, then confirm updating its evidence. Any existing approval will be invalidated.")
        s = case_from_vendor(store, vendor_id, form)
        if old:
            previous = pipeline.scenarios[old["id"]]
            s.id = old["id"]
            store.save_record("case_revision", {"id": s.id + "-" + old["revision"], "case_id": s.id,
                "saved_at": utcnow(), "scenario": previous.model_dump(mode="json"), "claim": store.get_claim(s.id)})
        s.summary = f"{v['customer']}: consolidated {s.period} evidence"
        log_ids = sorted(set(([form.evidence_id] if form.evidence_id else []) + form.evidence_ids))
        ticket_ids = sorted(set(([form.ticket_id] if form.ticket_id else []) + form.ticket_ids))
        record = {"id": s.id, "vendor_id": vendor_id, "identity": scenario_identity(s), "read_id": v["read_id"],
            "invoice_id": form.invoice_id or v["invoice_id"], "receiving_invoice_id": form.receiving_invoice_id,
            "form": form.model_dump(mode="json"),
            "evidence_ids": log_ids, "ticket_ids": ticket_ids, "fingerprint": fingerprint, "updated_at": utcnow()}
        pipeline.add_user_case(s)
        store.save_record("monthly_case", record)
        # run() invalidates an older draft/approval if the decision's inputs changed.
        return pipeline.detail(s.id) | {"id": s.id, "reused": False, "monthly": record}
