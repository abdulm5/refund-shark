import hashlib
import re
from typing import Literal

from shark.models import SLATerms

# Non-contiguous quotes join verbatim fragments with this marker; each fragment must appear.
FRAGMENT_MARKER = "[…]"
TermStatus = Literal["verified", "unverified", "missing", "absent"]


def source_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.translate(str.maketrans("‘’“”", "''\"\""))).strip()


def quote_found(quote: str | None, normalized_source: str) -> bool:
    fragments = [normalize(part) for part in (quote or "").split(FRAGMENT_MARKER)]
    return bool(quote) and all(fragments) and all(part in normalized_source for part in fragments)


def verify_citations(terms: SLATerms, source: str) -> dict[str, bool]:
    normalized = normalize(source)
    return {name: quote_found(getattr(terms, name).quote, normalized) for name in type(terms).model_fields}


def term_status(terms: SLATerms, source: str) -> dict[str, TermStatus]:
    """Keep a confirmed absence distinct from a failed or missing extraction."""
    quoted = verify_citations(terms, source)
    result: dict[str, TermStatus] = {}
    for name in type(terms).model_fields:
        field = getattr(terms, name)
        if field.absent:
            result[name] = "absent"
        elif field.value is None:
            result[name] = "missing"
        else:
            result[name] = "verified" if quoted[name] else "unverified"
    return result


def validate_rules(terms: SLATerms) -> list[str]:
    errors = []
    commitment = terms.uptime_commitment_pct.value
    if commitment is None or not 0 <= commitment <= 100:
        errors.append("Uptime commitment is missing or outside 0–100%")
    tiers = sorted(terms.credit_tiers.value or [], key=lambda t: t.lower)
    for previous, current in zip(tiers, tiers[1:]):
        if previous.upper > current.lower or (
            previous.upper == current.lower and previous.upper_inclusive and current.lower_inclusive
        ):
            errors.append("Credit tiers overlap")
    # A gap can be written in a contract: do not invent an entitlement to fill it.
    return errors
