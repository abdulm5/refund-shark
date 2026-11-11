"""Find which watched vendors a business pays, from Ramp spend data, reading only fields Ramp's public API reference documents.

Every field this module reads is registered in CITED with the verbatim line of Ramp's published reference
that documents it; `read` refuses anything else, and a test checks each line against the committed excerpt
(data/sources/ramp-developer-api.excerpt.txt). Spend shows that a business pays a vendor, never which
product or plan: the plan stays unknown, so an outage can make the customer "possibly affected", no more.
Amounts are not read: the reference does not state the unit of a transaction amount.
"""
import hashlib
import json
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Protocol
from urllib.parse import urlsplit

import httpx

from shark.models import Customer, Subscription
from shark.spend.constants import UNKNOWN_PLAN
from shark.pipeline import ROOT

EXCERPT = ROOT / "data/sources/ramp-developer-api.excerpt.txt"
RECORD = ROOT / "data/sources/ramp-developer-api.json"
SAMPLE = ROOT / "data/fixtures/ramp/sample-business.json"
API_BASES = {"production": "https://api.ramp.com", "sandbox": "https://demo-api.ramp.com"}

BUSINESS = "Fetch the company information · /developer/v1/business"
VENDORS = "List vendors · /developer/v1/vendors"
TRANSACTIONS = "List transactions · /developer/v1/transactions"
# (excerpt block, field) -> the verbatim line of the public reference that documents it.
CITED = {
    (BUSINESS, "business_name_on_card"): "business_name_on_card?: string",
    (BUSINESS, "business_name_legal"): "business_name_legal?: string",
    (VENDORS, "name"): "name: string",
    (VENDORS, "is_active"): "is_active: boolean",
    (VENDORS, "billing_frequency"): "billing_frequency?: 'ANNUAL' | 'MONTHLY' | 'MULTIPLE' | 'NA' | 'OTHER' | 'QUARTERLY' | 'ROLLING' | 'TBD' | 'TWICE_A_YEAR'",
    (VENDORS, "data"): "data: {",
    (VENDORS, "page"): "page: {",
    (VENDORS, "next"): "next: string | null",
    (TRANSACTIONS, "merchant_name"): "merchant_name?: string | null",
    (TRANSACTIONS, "user_transaction_time"): "user_transaction_time?: string /* date */",
    (TRANSACTIONS, "state"): "state?: 'ALL' | 'CLEARED' | 'COMPLETION' | 'DECLINED' | 'ERROR' | 'PENDING' | 'PENDING_INITIATION'",
    (TRANSACTIONS, "data"): "data: {",
    (TRANSACTIONS, "page"): "page: {",
    (TRANSACTIONS, "next"): "next: string | null",
    (TRANSACTIONS, "declined"): "description: This endpoint supports filtering and ordering. If state is not set, all transactions except declined transactions will be returned. Note that setting multiple ordering parameters is unsupported.",
    ("Pagination", "next"): "Use the complete URL in the `next` field to request the next page. The URL includes the `start` cursor and preserves the other query parameters required for the current result set. Treat the cursor as opaque instead of constructing or changing it yourself.",
    ("Pagination", "page_size"): 'curl "https://api.ramp.com/developer/v1/transactions?page_size=100" \\',
    ("Quickstart", "production"): "| Production | `https://api.ramp.com` | `https://app.ramp.com` |",
    ("Quickstart", "sandbox"): "| Sandbox (demo) | `https://demo-api.ramp.com` | `https://demo.ramp.com` |",
    ("Quickstart", "bearer"): "Add the header `Authorization: Bearer <YOUR_TOKEN_HERE>` and make a GET request to `{environment-url}/developer/v1/transactions`.",
    ("Authorization", "business:read"): "| `business:read` | Business profile | Access business information and settings |",
    ("Authorization", "transactions:read"): "| `transactions:read` | Transactions | Access transaction data and history |",
    ("Authorization", "vendors:read"): "| `vendors:read` | Vendors | View vendor information and payment details |",
}
SCOPES = "business:read transactions:read vendors:read"

# Watched vendors by the name they bill under. Exact names only: a memo or descriptor never decides a vendor or a plan.
VENDOR_NAMES = {"atlassian": "Atlassian", "github": "GitHub", "cloudflare": "Cloudflare", "digitalocean": "DigitalOcean",
                "amazon web services": "AWS", "aws": "AWS", "google cloud": "Google Cloud", "expo": "Expo"}
SUFFIXES = {"inc", "llc", "ltd", "pty", "corp", "corporation", "co", "limited", "plc", "gmbh", "bv", "sa"}
CYCLES = {"ANNUAL": "annual", "MONTHLY": "monthly"}


def read(record: dict, block: str, field: str):
    if (block, field) not in CITED:
        raise KeyError(f"{block}: {field} is not cited from Ramp's public API reference")
    return record.get(field)


def excerpt_blocks() -> dict[str, set[str]]:
    """The committed excerpt as {block: verbatim lines}; a block starts with a '## <block> | <url>' header."""
    blocks, current = {}, None
    for line in EXCERPT.read_text().splitlines():
        if line.startswith("## "):
            current = line[3:].split(" | ")[0]
            blocks[current] = set()
        elif current is not None and line.strip() and line.strip() != "[…]":
            blocks[current].add(line.strip())
    return blocks


def vendor_name(name: str | None) -> str | None:
    words = re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).split()
    while words and words[-1] in SUFFIXES:
        words.pop()
    return VENDOR_NAMES.get(" ".join(words))


class RampSource(Protocol):
    def business(self) -> dict: ...
    def vendors(self) -> Iterable[dict]: ...
    def transactions(self) -> Iterable[dict]: ...


def http_get(url: str, headers: dict, timeout: float) -> dict:
    response = httpx.get(url, headers=headers, timeout=timeout)
    response.raise_for_status()
    return response.json()


class HttpRamp:
    """Ramp's Developer API with an access token. The token goes only to the configured API host."""

    def __init__(self, token: str, base: str = API_BASES["production"], get=http_get, max_pages: int = 200):
        if base not in API_BASES.values():
            raise ValueError("Use Ramp's production or sandbox API host")
        self.token, self.base, self.get, self.max_pages = token, base, get, max_pages

    def _get(self, url: str) -> dict:
        if urlsplit(url)[:2] != urlsplit(self.base)[:2]:
            raise ValueError("Ramp returned a page on another host; the token is not sent there")
        return self.get(url, {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}, 20)

    def _pages(self, url: str, block: str) -> Iterable[dict]:
        for _ in range(self.max_pages):
            payload = self._get(url)
            yield from read(payload, block, "data") or []
            url = read(read(payload, block, "page") or {}, block, "next")
            if not url:
                return
        raise ValueError(f"More than {self.max_pages} pages; stopping")

    def business(self) -> dict:
        return self._get(f"{self.base}/developer/v1/business")

    def vendors(self) -> Iterable[dict]:
        return self._pages(f"{self.base}/developer/v1/vendors", VENDORS)

    def transactions(self) -> Iterable[dict]:
        return self._pages(f"{self.base}/developer/v1/transactions?page_size=100", TRANSACTIONS)


class FixtureRamp:
    """Responses saved in the documented shapes ({"data": [...], "page": {...}}), for demos and tests."""

    def __init__(self, path: Path):
        self.saved = json.loads(path.read_text())
        self.as_of = datetime.fromisoformat(self.saved["as_of"].replace("Z", "+00:00"))  # "the last 12 months" ends when the data was saved

    def business(self) -> dict:
        return self.saved["business"]

    def vendors(self) -> Iterable[dict]:
        return read(self.saved["vendors"], VENDORS, "data")

    def transactions(self) -> Iterable[dict]:
        return read(self.saved["transactions"], TRANSACTIONS, "data")


def import_business(source: RampSource, simulated: bool, now: datetime | None = None) -> tuple[Customer, dict]:
    """One workspace customer per Ramp business: a subscription for each watched vendor its spend shows, plan unknown."""
    now = now or getattr(source, "as_of", None) or datetime.now(timezone.utc)
    business = source.business()
    name = read(business, BUSINESS, "business_name_on_card") or read(business, BUSINESS, "business_name_legal") or "Ramp business"
    cycles, seen = {}, set()
    for vendor in source.vendors():
        canonical = vendor_name(read(vendor, VENDORS, "name"))
        if read(vendor, VENDORS, "is_active") is False:
            continue
        if canonical:
            cycles[canonical] = CYCLES.get(read(vendor, VENDORS, "billing_frequency") or "", "unknown")
        else:
            seen.add(read(vendor, VENDORS, "name"))
    charges: dict[str, list[datetime]] = defaultdict(list)
    for transaction in source.transactions():
        if read(transaction, TRANSACTIONS, "state") in {"DECLINED", "ERROR"}:
            continue
        merchant = read(transaction, TRANSACTIONS, "merchant_name")
        canonical = vendor_name(merchant)
        if merchant and not canonical:
            seen.add(merchant)  # reported as not watched, dated or not
        when = read(transaction, TRANSACTIONS, "user_transaction_time")
        at = datetime.fromisoformat(when.replace("Z", "+00:00")) if when else None
        if at is not None and at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        if at is None or at < now - timedelta(days=365):
            continue
        if canonical:
            charges[canonical].append(at)
    subscriptions = []
    for vendor in sorted(set(cycles) | set(charges)):
        times = sorted(charges.get(vendor, []))
        cycle = cycles.get(vendor, "unknown")
        evidence = (f"Ramp: {len(times)} charge{'' if len(times) == 1 else 's'} from {vendor} in the last 12 months, latest {times[-1]:%b %d, %Y}"
                    if times else f"Ramp: listed as a vendor; no charges in the last 12 months")
        evidence += {"annual": "; billed annually", "monthly": "; billed monthly"}.get(cycle, "") + ". Plan and product are not in spend data."
        subscriptions.append(Subscription(vendor=vendor, product=None, plan=UNKNOWN_PLAN, billing_cycle=cycle, evidence=evidence))
    customer_id = "ramp-" + (re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "business")
    customer = Customer(customer_id=customer_id, display_name=name, contact="Ramp business admin" + (" (demo)" if simulated else ""),
                        subscriptions=subscriptions, simulated=simulated, source="ramp")
    return customer, {"watched": [s.vendor for s in subscriptions], "not_watched": sorted(n for n in seen if n)}


def verify_excerpt() -> dict:
    """The committed excerpt is unchanged, and (when the docs are cached here) every quoted line is in its document."""
    record = json.loads(RECORD.read_text())
    from shark.extract.citations import source_hash
    excerpt = EXCERPT.read_text()
    result = {"id": "ramp-developer-api", "excerpt_intact": source_hash(excerpt) == record["excerpt_sha256"], "snapshot": "not_cached"}
    docs = {d["url"]: d for d in record["documents"]}
    texts = {}
    for url, doc in docs.items():
        path = ROOT / doc["cache_path"]
        if not path.exists():
            return result
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != doc["sha256"]:
            result["snapshot"] = "raw_hash_differs"
            return result
        texts[url] = {line.strip() for line in raw.decode().splitlines()}
    missing, url = 0, None
    for line in excerpt.splitlines():
        if line.startswith("## "):
            url = line.split(" | ")[-1]
        elif line.strip() and line.strip() != "[…]" and line.strip() not in texts.get(url, set()):
            missing += 1
    result["snapshot"], result["missing_lines"] = ("verified" if not missing else "text_differs"), missing
    return result
