"""The schema the model fills, kept small enough for structured outputs to compile.

The full `SLATerms` model (Decimals, nullable quotes, generic wrappers) compiles to a grammar the API
rejects as too large. The model instead returns numbers as strings and empty strings for "none";
`to_terms` converts that into `SLATerms`, where every value is validated by code as before.
"""
from decimal import Decimal, InvalidOperation
from typing import Literal

from pydantic import BaseModel, ConfigDict, create_model

from shark.models import SLATerms


# Identifiers the engine and drafter act on. Anything else is "other", described in the quote.
PREREQUISITES = Literal["support_ticket_during_incident", "account_fully_paid", "production_instance", "authorized_requester", "other"]
EXCLUSION_KINDS = Literal["scheduled_maintenance", "emergency_maintenance", "customer_breach", "not_provisioned", "unauthorized_use", "force_majeure",
                          "customer_infrastructure", "customer_data", "third_party_products", "excluded_features", "sandbox_or_free_plan", "other"]
CLAIM_FIELDS = Literal["account_reference", "service", "period", "incident_times", "calculation", "affected_resources", "request_logs", "other"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Evidence(Strict):
    quote: str  # copied exactly; "" when nothing supports the value
    section: str
    absent: bool  # true only when the source plainly contains no such term
    missing_reason: str


class XTier(Strict):
    lower: str
    upper: str
    lower_inclusive: bool
    upper_inclusive: bool
    credit_pct: str


class XMetric(Strict):
    kind: Literal["availability_minutes", "request_error_rate", "visitor_ratio", "other"]
    interval_seconds: int
    error_threshold_pct: str
    description: str
    covered_experiences: list[str]


class XCap(Strict):
    pct: str
    basis: Literal["affected_period_fees", "receiving_invoice", "other"]


class XMinimum(Strict):
    amount: str
    inclusive: bool


class XDeadline(Strict):
    kind: Literal["days_after_period", "billing_cycles_after", "other"]
    value: int
    timezone: str  # "" when the clause names none


class XExclusion(Strict):
    kind: EXCLUSION_KINDS
    description: str
    machine_checkable: bool


class XProcedure(Strict):
    channel: str
    required_fields: list[CLAIM_FIELDS]
    subject_line: str


class TextTerm(Evidence):
    value: str | None


class ListTerm(Evidence):
    value: list[str] | None


class PrerequisitesTerm(Evidence):
    value: list[PREREQUISITES] | None


class PeriodTerm(Evidence):
    value: Literal["calendar_month", "calendar_quarter", "billing_cycle", "other"] | None


class MetricTerm(Evidence):
    value: XMetric | None


class TiersTerm(Evidence):
    value: list[XTier] | None


class BasisTerm(Evidence):
    value: Literal["affected_service", "whole_bill", "other"] | None


class CapTerm(Evidence):
    value: XCap | None


class MinimumTerm(Evidence):
    value: XMinimum | None


class ExclusionsTerm(Evidence):
    value: list[XExclusion] | None


class DeadlineTerm(Evidence):
    value: XDeadline | None


class ProcedureTerm(Evidence):
    value: XProcedure | None


class RemedyTerm(Evidence):
    value: Literal["service_credit", "refund", "other"] | None


class ExtractionTerms(Strict):
    vendor: TextTerm
    covered_services: ListTerm
    plan: TextTerm
    uptime_commitment_pct: TextTerm
    measurement_period: PeriodTerm
    timezone: TextTerm
    downtime_definition: MetricTerm
    credit_tiers: TiersTerm
    credit_basis: BasisTerm
    credit_cap: CapTerm
    min_credit: MinimumTerm
    exclusions: ExclusionsTerm
    prerequisites: PrerequisitesTerm
    claim_window: DeadlineTerm
    claim_procedure: ProcedureTerm
    remedy_form: RemedyTerm


def _group(name: str, fields: list[str]):
    return create_model(name, __config__=ConfigDict(extra="forbid"), **{f: (ExtractionTerms.model_fields[f].annotation, ...) for f in fields})


# One request per group: the whole schema at once compiles to a grammar the API rejects as too large.
GROUPS = {
    "commitment": _group("CommitmentTerms", ["vendor", "covered_services", "plan", "uptime_commitment_pct", "measurement_period", "timezone",
                                             "downtime_definition", "credit_tiers"]),
    "pricing": _group("PricingTerms", ["credit_basis", "credit_cap", "min_credit", "exclusions"]),
    "process": _group("ProcessTerms", ["prerequisites", "claim_window", "claim_procedure", "remedy_form"]),
}


def from_terms(terms: SLATerms) -> ExtractionTerms:
    """The inverse of to_terms, for tests and comparisons."""
    fields = {}
    for name in ExtractionTerms.model_fields:
        cited = getattr(terms, name)
        value = cited.value
        if value is not None:
            if name == "uptime_commitment_pct":
                value = str(value)
            elif name == "credit_tiers":
                value = [{"lower": str(t.lower), "upper": str(t.upper), "lower_inclusive": t.lower_inclusive, "upper_inclusive": t.upper_inclusive,
                          "credit_pct": str(t.credit_pct)} for t in value]
            elif name == "downtime_definition":
                value = {**value.model_dump(), "error_threshold_pct": "" if value.error_threshold_pct is None else str(value.error_threshold_pct)}
            elif name == "credit_cap":
                value = {"pct": str(value.pct), "basis": value.basis}
            elif name == "min_credit":
                value = {"amount": str(value.amount), "inclusive": value.inclusive}
            elif name == "claim_window":
                value = {**value.model_dump(), "timezone": value.timezone or ""}
            elif name == "claim_procedure":
                value = {**value.model_dump(), "subject_line": value.subject_line or ""}
            elif name == "exclusions":
                value = [e.model_dump() for e in value]
        fields[name] = {"value": value, "quote": cited.quote or "", "section": cited.section or "", "absent": cited.absent,
                        "missing_reason": cited.missing_reason or ""}
    return ExtractionTerms.model_validate(fields)


def number(text: str) -> Decimal:
    cleaned = text.strip().rstrip("%").replace(",", "").lstrip("$").strip()
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        raise ValueError(f"Not a number: {text!r}") from None


def to_terms(x: ExtractionTerms) -> tuple[SLATerms, list[str]]:
    """Convert to SLATerms. A value that fails conversion becomes missing (so the engine reviews) and is noted."""
    notes: list[str] = []
    fields = {}
    for name in ExtractionTerms.model_fields:
        term = getattr(x, name)
        value = term.value
        try:
            if value is not None:
                if name == "uptime_commitment_pct":
                    value = number(value)
                elif name == "credit_tiers":
                    value = [{"lower": number(t.lower), "upper": number(t.upper), "lower_inclusive": t.lower_inclusive,
                              "upper_inclusive": t.upper_inclusive, "credit_pct": number(t.credit_pct)} for t in value]
                elif name == "downtime_definition":
                    value = {**value.model_dump(), "error_threshold_pct": number(value.error_threshold_pct) if value.error_threshold_pct.strip() else None}
                elif name == "credit_cap":
                    value = {"pct": number(value.pct), "basis": value.basis}
                elif name == "min_credit":
                    value = {"amount": number(value.amount), "inclusive": value.inclusive}
                elif name == "claim_window":
                    value = {**value.model_dump(), "timezone": value.timezone.strip() or None}
                elif name == "claim_procedure":
                    value = {**value.model_dump(), "subject_line": value.subject_line.strip() or None}
                elif isinstance(value, BaseModel) or (isinstance(value, list) and value and isinstance(value[0], BaseModel)):
                    value = value.model_dump() if isinstance(value, BaseModel) else [v.model_dump() for v in value]
        except ValueError as error:
            notes.append(f"{name}: {error}")
            value = None
        absent = term.absent and value is None
        fields[name] = {"value": value, "quote": term.quote or None, "section": term.section or None,
                        "absent": absent, "missing_reason": term.missing_reason or None}
    try:
        return SLATerms.model_validate(fields), notes
    except ValueError:
        # Field-level validation (for example a tier with lower >= upper): drop only the failing values.
        terms = {}
        for name, field in fields.items():
            try:
                SLATerms.model_fields[name].annotation.model_validate(field)
                terms[name] = field
            except ValueError as error:
                detail = error.errors()[0] if hasattr(error, "errors") else None
                where = ".".join(str(part) for part in detail["loc"]) if detail else ""
                notes.append(f"{name}: {where}: {detail['msg']}" if detail else f"{name}: {error}")
                terms[name] = {**field, "value": None, "absent": False}
        return SLATerms.model_validate(terms), notes
