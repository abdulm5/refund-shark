"""The flat extraction schema round-trips to SLATerms, and conversion failures become missing terms."""
from decimal import Decimal

from conftest import scenario
from shark.extract.schema import GROUPS, ExtractionTerms, from_terms, to_terms


def test_round_trip_preserves_every_term():
    terms = scenario("jira-sept-claim").terms
    converted, notes = to_terms(from_terms(terms))
    assert notes == [] and converted == terms
    assert converted.min_credit.absent and converted.credit_tiers.value[0].credit_pct == Decimal(10)


def test_groups_cover_every_field_once():
    names = [name for group in GROUPS.values() for name in group.model_fields]
    assert sorted(names) == sorted(ExtractionTerms.model_fields) and len(names) == len(set(names))


def test_unreadable_number_becomes_missing_with_a_note():
    x = from_terms(scenario("jira-sept-claim").terms)
    x.credit_tiers.value[0].credit_pct = "ten"
    x.uptime_commitment_pct.value = "99.9%"  # a percent sign is fine
    terms, notes = to_terms(x)
    assert terms.credit_tiers.value is None and not terms.credit_tiers.absent
    assert terms.uptime_commitment_pct.value == Decimal("99.9") and notes == ["credit_tiers: Not a number: 'ten'"]


def test_invalid_tier_bounds_drop_only_that_term():
    x = from_terms(scenario("jira-sept-claim").terms)
    x.credit_tiers.value[0].lower, x.credit_tiers.value[0].upper = "99.9", "99.0"
    terms, notes = to_terms(x)
    assert terms.credit_tiers.value is None and terms.vendor.value == "Atlassian" and notes and notes[0].startswith("credit_tiers")


def test_engine_identifiers_are_a_fixed_vocabulary():
    from typing import get_args
    from shark.extract.schema import CLAIM_FIELDS, EXCLUSION_KINDS, PREREQUISITES
    assert {"support_ticket_during_incident", "account_fully_paid", "production_instance", "other"} <= set(get_args(PREREQUISITES))
    assert "scheduled_maintenance" in get_args(EXCLUSION_KINDS) and "other" in get_args(EXCLUSION_KINDS)
    assert {"account_reference", "service", "period", "incident_times", "calculation"} <= set(get_args(CLAIM_FIELDS))


def test_validation_notes_name_the_failing_field_and_rule():
    x = from_terms(scenario("jira-sept-claim").terms)
    x.downtime_definition.value.interval_seconds = 0
    terms, notes = to_terms(x)
    assert terms.downtime_definition.value is None
    assert notes and "interval_seconds" in notes[0] and "greater than 0" in notes[0]
