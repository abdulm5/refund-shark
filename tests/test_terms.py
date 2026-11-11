
import pytest
from pydantic import ValidationError

from shark.engine import evaluate
from shark.extract.citations import term_status, verify_citations
from shark.models import Cited


def absent(reason="The agreement states no such term."):
    return Cited(value=None, quote=None, section=None, absent=True, missing_reason=reason)


def test_confirmed_absent_minimum_is_not_a_failed_extraction(case):
    case.terms.min_credit = absent("Neither source states a minimum credit amount.")
    assert term_status(case.terms, case.source_text)["min_credit"] == "absent"
    d = evaluate(case)
    assert d.outcome == "eligible"
    minimum = next(c for c in d.checks if c.field == "min_credit")
    assert minimum.status == "pass" and "No minimum" in minimum.detail


def test_unextracted_minimum_needs_review(case):
    case.terms.min_credit = Cited(value=None, quote=None, section=None)
    assert term_status(case.terms, case.source_text)["min_credit"] == "missing"
    d = evaluate(case)
    assert d.outcome == "needs_review"
    assert any(c.field == "min_credit" and c.status == "review" for c in d.checks)


def test_absent_required_term_needs_review(case):
    case.terms.credit_tiers = absent()
    d = evaluate(case)
    assert d.outcome == "needs_review"
    assert any(c.field == "credit_tiers" and "states no" in c.detail for c in d.checks)


def test_absent_cap_means_uncapped(case):
    expected = evaluate(case).amount
    case.terms.credit_cap = absent()
    d = evaluate(case)
    assert d.outcome == "eligible"
    assert d.amount == expected
    assert any(c.field == "credit_cap" and c.status == "pass" and "No cap" in c.detail for c in d.checks)


def test_absent_value_must_be_empty():
    with pytest.raises(ValidationError):
        Cited(value="99.9", quote=None, section=None, absent=True)


def test_quote_fragments_must_each_appear(case):
    first, second = case.terms.vendor.quote, case.terms.plan.quote
    case.terms.vendor.quote = f"{first} […] {second}"
    assert verify_citations(case.terms, case.source_text)["vendor"] is True
    case.terms.vendor.quote = f"{first} […] This sentence is not in the source."
    assert verify_citations(case.terms, case.source_text)["vendor"] is False
    assert term_status(case.terms, case.source_text)["vendor"] == "unverified"
