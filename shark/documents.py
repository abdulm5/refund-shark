"""Local PDFs with page-level provenance and explicitly reviewed invoice fields."""
import hashlib
import io
import re
from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

import pdfplumber
from pydantic import BaseModel, ConfigDict, Field, model_validator

from shark.extract.citations import normalize
from shark.storage import utcnow

MAX_PDF_BYTES = 10_000_000
MAX_PAGES = 60
MAX_TEXT = 250_000
MONTH = r"^\d{4}-(0[1-9]|1[0-2])$"
Money = Annotated[Decimal, Field(ge=0, max_digits=14, decimal_places=2)]


class InvoiceFields(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    vendor: str = Field(min_length=1, max_length=120)
    customer: str = Field(min_length=1, max_length=120)
    account_reference: str = Field(min_length=1, max_length=120)
    product: str = Field(min_length=1, max_length=120)
    plan: str = Field(min_length=1, max_length=120)
    invoice_number: str = Field(min_length=1, max_length=120)
    period: str = Field(pattern=MONTH)
    issued_on: date
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    billing_cycle: Literal["monthly", "annual"] = "monthly"
    fees: Money
    credit_amount: Money = Decimal("0")
    credit_period: str | None = Field(default=None, pattern=MONTH)
    claim_reference: str | None = Field(default=None, max_length=120)
    reviewed: Literal[True]

    @model_validator(mode="after")
    def credit_identity(self):
        if self.credit_amount and not (self.credit_period and self.claim_reference):
            raise ValueError("A credit line needs its claim reference and original claim month.")
        return self


LABELS = {
    "vendor": "Vendor", "customer": "Customer", "account_reference": "Account", "product": "Product", "plan": "Plan",
    "invoice_number": "Invoice number", "period": "Billing period", "issued_on": "Issue date", "currency": "Currency",
    "billing_cycle": "Billing cycle", "fees": "Product fee", "credit_amount": "Service credit", "credit_period": "Credit period",
    "claim_reference": "Claim reference",
}


def suggestions(pages: list[dict]) -> dict:
    """Read explicit labels, never a guessed total/tax/credit. Ambiguous repeated values stay empty."""
    result = {}
    for field, label in LABELS.items():
        matches = []
        for page in pages:
            for line in page["text"].splitlines():
                m = re.fullmatch(rf"\s*{re.escape(label)}\s*:\s*(.+?)\s*", line, re.I)
                if m:
                    value = m[1]
                    if field in {"fees", "credit_amount"}:
                        value = value.replace(",", "").removeprefix("$").strip()
                        if not re.fullmatch(r"\d+(?:\.\d{1,2})?", value):
                            continue
                    matches.append({"value": value, "page": page["number"], "quote": line.strip()})
        if len({m["value"] for m in matches}) == 1:
            result[field] = matches[0]
    return result


def parse_pdf(content: bytes, filename: str, kind: str) -> dict:
    if not content.startswith(b"%PDF-"):
        raise ValueError("Choose a PDF file. Renaming another file to .pdf does not convert it.")
    if len(content) > MAX_PDF_BYTES:
        raise ValueError("PDFs must be 10 MB or smaller.")
    pages, total = [], 0
    try:
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            if pdf.doc.encryption:
                raise ValueError("Export an unencrypted copy of this PDF before uploading.")
            if not 0 < len(pdf.pages) <= MAX_PAGES:
                raise ValueError(f"Upload a PDF with 1–{MAX_PAGES} pages.")
            for i, p in enumerate(pdf.pages):
                text = p.extract_text() or ""
                total += len(text)
                if total > MAX_TEXT:
                    raise ValueError("This PDF contains too much text. Upload just the agreement or invoice pages.")
                pages.append({"number": i + 1, "text": text})
                p.close()
    except ValueError:
        raise
    except Exception:
        raise ValueError("This PDF could not be read. Export an unencrypted, searchable PDF and try again.") from None
    if any(len(p["text"].strip()) < 10 for p in pages):
        raise ValueError("At least one page has no readable text. OCR scanned pages or remove blank pages before uploading; no pages are silently skipped.")
    digest = hashlib.sha256(content).hexdigest()
    return {"id": f"doc-{kind}-{digest[:24]}", "kind": kind, "filename": filename.replace('\\', '/').split('/')[-1][:160],
            "sha256": digest, "bytes": len(content), "pages": pages, "text": "\n\n".join(p["text"] for p in pages),
            "created_at": utcnow(), "simulated": any("SIMULATED SAMPLE" in p["text"] for p in pages),
            "suggestions": suggestions(pages) if kind == "invoice" else {}, "confirmed": None}


def citation_pages(terms: dict, pages: list[dict]) -> dict:
    result = {}
    # Fragments can span a page break. Map the exact normalized match back to its page spans.
    full, spans = "", []
    for p in pages:
        text = normalize(p["text"])
        start = len(full)
        full += text + " "
        spans.append((start, len(full), p["number"]))
    for name, term in terms.items():
        found = set()
        for part in (term.get("quote") or "").split("[…]"):
            part = normalize(part)
            start = full.find(part) if part else -1
            if start >= 0:
                found.update(n for a, b, n in spans if a < start + len(part) and b > start)
        result[name] = sorted(found)
    return result


def public_document(doc: dict, detail: bool = False) -> dict:
    return {k: v for k, v in doc.items() if k != "text" and (detail or k not in {"pages", "suggestions"})} | {"page_count": len(doc["pages"])}
