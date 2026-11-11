"""Immutable human-reviewed agreement versions, pinned to their original source."""
import copy
import json
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from shark.documents import citation_pages
from shark.engine.readiness import readiness
from shark.extract.citations import term_status, validate_rules
from shark.models import SLATerms
from shark.storage import utcnow


class ReviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    terms: SLATerms
    reviewer: str = Field(min_length=1, max_length=120)
    reason: str = Field(min_length=3, max_length=2000)
    confirmed: bool


def review_family(store, read):
    root = read.get("review", {}).get("root_id", read["id"])
    return sorted([r for r in store.reads() if r["id"] == root or r.get("review", {}).get("root_id") == root],
                  key=lambda r: r.get("review", {}).get("version", 0))


def read_detail(store, read_id):
    read = store.get_read(read_id)
    if not read:
        raise ValueError("That saved agreement was not found.")
    status = term_status(SLATerms.model_validate(read["terms"]), read["text"])
    doc = store.record("document", read["source"].get("document_id"))
    family = review_family(store, read)
    return {**read, "term_status": status, "readiness": readiness(SLATerms.model_validate(read["terms"]), status),
            "pages": (doc or {}).get("pages", []), "latest_id": family[-1]["id"],
            "history": [{"id": r["id"], "review": r.get("review")} for r in family]}


def approve_review(store, read_id, form: ReviewInput):
    original = read_detail(store, read_id)
    if not form.confirmed:
        raise ValueError("Review every term and confirm before approving this version.")
    if original["latest_id"] != read_id:
        raise ValueError("A newer review exists. Open the latest version before making changes.")
    statuses = term_status(form.terms, original["text"])
    bad = [name for name, state in statuses.items() if state == "unverified"]
    if bad:
        raise ValueError("Supporting quotes must appear in the saved source: " + ", ".join(bad))
    for name, field in type(form.terms).model_fields.items():
        term = getattr(form.terms, name)
        if term.value is None and not (term.missing_reason or "").strip():
            raise ValueError(f"Explain why {name.replace('_', ' ')} is absent or still unknown.")
    errors = validate_rules(form.terms)
    # Unknown commitment is reviewable, but invalid numbers and overlapping tiers are not.
    errors = [e for e in errors if not (form.terms.uptime_commitment_pct.value is None and e.startswith("Uptime commitment"))]
    if errors:
        raise ValueError("; ".join(errors))
    if form.terms.plan.value and form.terms.plan.value.casefold() != original["selection"]["plan"].casefold():
        raise ValueError("The reviewed plan must match this agreement's selected plan.")
    if form.terms.covered_services.value and original["selection"]["service"].casefold() not in [s.casefold() for s in form.terms.covered_services.value]:
        raise ValueError("The selected product must appear in the reviewed covered services.")
    updated = copy.deepcopy(store.get_read(read_id))
    updated["id"] = "read-reviewed-" + uuid4().hex[:16]
    updated["terms"] = form.terms.model_dump(mode="json")
    updated["review"] = {"root_id": original.get("review", {}).get("root_id", read_id), "parent_id": read_id,
        "version": original.get("review", {}).get("version", 0) + 1, "reviewer": form.reviewer, "reason": form.reason,
        "approved_at": utcnow(), "changed_fields": [k for k in updated["terms"] if updated["terms"][k] != original["terms"][k]]}
    if original["pages"]:
        updated["page_citations"] = citation_pages(updated["terms"], original["pages"])
    # Serialize lineage checks with the write, so two tabs cannot create competing next versions.
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        rows = [json.loads(r[0]) for r in db.execute("SELECT payload FROM agreement_reads")]
        if any(r.get("review", {}).get("parent_id") == read_id for r in rows):
            raise ValueError("A newer review exists. Open the latest version before making changes.")
        db.execute("INSERT INTO agreement_reads VALUES(?,?,?)", (updated["id"], json.dumps(updated), utcnow()))
        store.event("review", "Agreement version approved", f"Version {updated['review']['version']} · {form.reviewer} · {form.reason}", db)
    return read_detail(store, updated["id"])
