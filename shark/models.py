from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Generic, Literal, TypeVar

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

T = TypeVar("T")


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Cited(Model, Generic[T]):
    value: T | None
    quote: str | None
    section: str | None
    # Confirmed absent: the reviewed source states no such term (e.g. no minimum credit).
    # Distinct from value=None alone, which means the term was not extracted.
    absent: bool = False
    missing_reason: str | None = None

    @model_validator(mode="after")
    def absence_has_no_value(self):
        if self.absent and self.value is not None:
            raise ValueError("A term confirmed absent cannot also carry a value")
        return self


class Tier(Model):
    lower: Decimal = Field(ge=0, le=100)
    upper: Decimal = Field(ge=0, le=100)
    lower_inclusive: bool = True
    upper_inclusive: bool = False
    credit_pct: Decimal = Field(ge=0, le=100)

    @model_validator(mode="after")
    def bounds(self):
        if self.lower >= self.upper:
            raise ValueError("Tier lower boundary must precede upper boundary")
        return self


class Metric(Model):
    kind: Literal["availability_minutes", "request_error_rate", "visitor_ratio", "other"]
    interval_seconds: int = Field(default=60, gt=0)
    error_threshold_pct: Decimal | None = Field(default=None, ge=0, le=100)
    description: str
    covered_experiences: list[str] = []  # empty: the agreement does not limit downtime to named experiences


class Cap(Model):
    pct: Decimal = Field(ge=0, le=100)
    basis: Literal["affected_period_fees", "receiving_invoice", "other"]


class Minimum(Model):
    amount: Decimal = Field(ge=0)
    inclusive: bool = True


class DeadlineRule(Model):
    kind: Literal["days_after_period", "billing_cycles_after", "other"]
    value: int = Field(ge=0)
    timezone: str | None


class Exclusion(Model):
    kind: str
    description: str
    machine_checkable: bool


class Procedure(Model):
    channel: str
    required_fields: list[str]
    subject_line: str | None


class DisputeRule(Model):
    business_days: int = Field(gt=0, le=366)  # counted from when the award is recorded


class SLATerms(Model):
    vendor: Cited[str]
    covered_services: Cited[list[str]]
    plan: Cited[str]
    uptime_commitment_pct: Cited[Decimal]
    measurement_period: Cited[Literal["calendar_month", "calendar_quarter", "billing_cycle", "other"]]
    timezone: Cited[str]
    downtime_definition: Cited[Metric]
    credit_tiers: Cited[list[Tier]]
    credit_basis: Cited[Literal["affected_service", "whole_bill", "other"]]
    credit_cap: Cited[Cap]
    min_credit: Cited[Minimum]
    exclusions: Cited[list[Exclusion]]
    prerequisites: Cited[list[str]]
    claim_window: Cited[DeadlineRule]
    claim_procedure: Cited[Procedure]
    remedy_form: Cited[Literal["service_credit", "refund", "other"]]


class Interval(Model):
    start: AwareDatetime
    end: AwareDatetime

    @model_validator(mode="after")
    def ordered(self):
        if self.end <= self.start:
            raise ValueError("Interval end must follow its start")
        return self


class Window(Model):
    label: str
    intervals: list[Interval]
    source_url: str | None = None
    detail: str = ""


class LogRef(Model):
    """A per-minute request log (minute,experience,requests,errors), pinned by hash."""
    path: str  # relative to the repository root
    sha256: str


class LogSummary(Model):
    rows: int
    minutes_covered: int  # distinct minutes of the period present in the log
    period_minutes: int
    downtime_minutes: int
    zero_request_minutes: int
    below_threshold_error_minutes: int
    excluded_error_minutes: dict[str, int]
    covered_experiences: list[str]
    threshold_pct: Decimal


class Incident(Model):
    """One evidence record: a public incident (discovery) or customer metrics (contractual minutes)."""
    id: str
    title: str
    service: str
    intervals: list[Interval]
    interval_basis: str = "Authored interval"  # provenance of `intervals`
    context_windows: list[Window] = []  # other documented windows: estimated separately, never counted
    severity: Literal["major_outage", "partial_outage", "degraded_performance", "operational"]
    source_url: str | None = None
    evidence_kind: Literal["customer_metrics", "public_timeline", "synthetic"]
    metric_verified: bool = False
    related_incident: str | None = None  # customer metrics that answer a public incident
    experiences: list[str] = []  # covered experiences named in customer metrics
    components: list[str] = []
    region: str | None = None
    started_at: AwareDatetime | None = None  # lets an incident with no closed window be placed in a period
    log: LogRef | None = None  # customer metrics computed from a raw log when scenarios load
    log_summary: LogSummary | None = None
    simulated: bool = True
    notes: str = ""


class Maintenance(Model):
    service: str
    interval: Interval
    exclusion_confirmed: bool
    reference: str | None = None
    simulated: bool = True


class Invoice(Model):
    id: str
    customer_id: str
    vendor: str
    service: str
    period: str
    currency: str = "USD"
    affected_fees: Decimal = Field(ge=0)
    fee_allocation: Literal["standalone", "bundled", "unknown"] = "standalone"
    annual_fees: Decimal | None = Field(default=None, ge=0)  # for annual billing: the product's annual fee
    simulated: bool = True


class AccountContext(Model):
    customer_id: str
    plan: str
    service: str
    prerequisites: dict[str, bool | None]
    history_complete: bool
    non_machine_exclusions_reviewed: bool = False
    receiving_invoice_fees: Decimal | None = Field(default=None, ge=0)
    account_reference: str | None = None
    billing_cycle: Literal["monthly", "annual", "unknown"] = "monthly"
    display_name: str | None = None


class SourceRef(Model):
    """A committed verbatim excerpt chained to the private snapshot it was cut from."""
    id: str
    title: str
    url: str
    retrieved_at: str
    effective_date: str | None
    raw_sha256: str
    text_sha256: str
    excerpt_sha256: str
    parser: str


class Provenance(Model):
    item: str
    kind: Literal["real", "simulated", "authored", "user"]  # user: entered or uploaded in this workspace
    detail: str


class Subscription(Model):
    vendor: str
    product: str | None  # None: spend shows the vendor but not which product, so any of its products may apply
    plan: str  # "unknown" when spend data shows the vendor but not the plan
    site_region: str | None = None  # where the vendor hosts this customer's site, when known
    account_reference: str | None = None
    billing_cycle: Literal["monthly", "annual", "unknown"] | None = None
    evidence: str | None = None  # where this subscription came from, for imported spend


class Customer(Model):
    """A workspace customer as spend data would show them: who pays which vendor for which plan."""
    customer_id: str
    display_name: str
    contact: str
    subscriptions: list[Subscription]
    simulated: bool = True
    source: Literal["directory", "ramp", "setup"] = "directory"


class Scenario(Model):
    id: str
    vendor: str
    product: str
    plan: str
    color: str
    initials: str
    summary: str
    period: str
    as_of: AwareDatetime
    source_text: str
    source_hash: str
    source_label: str
    official_sla_url: str | None = None
    terms: SLATerms
    account: AccountContext
    invoice: Invoice
    incidents: list[Incident]
    maintenance: list[Maintenance] = []
    simulated: bool = True
    role: Literal["claim_candidate", "refusal", "missing_evidence", "capability_boundary", "user_case"] = "capability_boundary"
    dispute_rule: Cited[DisputeRule] | None = None  # how long the vendor allows to contest an awarded credit
    source_refs: list[SourceRef] = []
    provenance: list[Provenance] = []


class Check(Model):
    name: str
    status: Literal["pass", "fail", "review"]
    detail: str
    field: str | None = None


class Estimate(Model):
    label: str
    counted: bool  # True: contractual evidence; False: provisional screening estimate
    downtime_minutes: Decimal
    uptime_pct: Decimal
    credit_pct: Decimal | None
    amount: Decimal


class Decision(Model):
    outcome: Literal["eligible", "not_eligible", "needs_review"]
    reason: str
    amount: Decimal = Decimal("0.00")
    estimated_amount: Decimal | None = None
    currency: str = "USD"
    downtime_minutes: Decimal = Decimal(0)
    excluded_minutes: Decimal = Decimal(0)
    period_minutes: Decimal = Decimal(0)
    uptime_pct: Decimal | None = None
    credit_pct: Decimal | None = None
    affected_fees: Decimal = Decimal(0)
    deadline: datetime | None = None
    deadline_latest: datetime | None = None  # differs from deadline only when the clause names no time zone
    checks: list[Check] = []
    citations: dict[str, bool] = {}
    term_status: dict[str, str] = {}
    calculation: list[dict[str, str]] = []
    estimates: list[Estimate] = []
    estimate_basis: str | None = None
    evidence_quality: str = "simulated"
    evaluated_at: datetime


class Claim(Model):
    id: str
    scenario_id: str
    decision_hash: str
    status: Literal["draft", "approved", "sent", "short_paid", "disputed", "credited", "rejected", "invalidated"]
    subject: str
    body: str
    tone: Literal["polite", "firm"] = "polite"
    amount: Decimal
    currency: str
    created_at: datetime
    updated_at: datetime
    events: list[dict[str, str]] = []
    simulated: bool = True
    award_amount: Decimal | None = None  # what the vendor awarded (simulated)
    dispute_deadline: datetime | None = None  # earliest reading of the revisit window
    dispute_deadline_latest: datetime | None = None
    dispute_body: str | None = None
