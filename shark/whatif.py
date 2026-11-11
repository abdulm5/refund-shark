"""What-if: price a case with an uploaded request log, without storing anything.

The upload replaces the case's customer metrics in a copy of the scenario, and the same `evaluate()`
that prices stored cases prices the copy. Stored decisions, claims, and approvals are never touched.
"""
from decimal import Decimal

from shark.engine import evaluate
from shark.engine.intervals import microseconds, month_bounds
from shark.engine.metrics import downtime_from_samples, parse_log
from shark.models import Incident, LogSummary, Scenario


def measure_upload(terms, period: str, csv_text: str):
    """Downtime Minutes for an uploaded log under an agreement's metric, with the same refusals everywhere."""
    metric = terms.downtime_definition.value
    if metric is None or metric.kind != "request_error_rate" or metric.error_threshold_pct is None:
        raise ValueError("This agreement's downtime is not a per-minute request error rate, so request logs cannot measure it.")
    start, end = month_bounds(period)
    samples = [x for x in parse_log(csv_text) if start <= x.minute < end]
    if not samples:
        raise ValueError(f"The log has no minutes in {period}, the billing period of this case.")
    covered = metric.covered_experiences or sorted({x.experience for x in samples})
    if not {x.experience for x in samples} & set(covered):
        # Pricing a log that measures nothing covered would read as zero downtime: refuse instead of guessing.
        raise ValueError(f"None of the log's experiences ({', '.join(sorted({x.experience for x in samples}))}) is a covered experience under this "
                         f"agreement ({', '.join(covered)}). Names must match exactly.")
    analysis = downtime_from_samples(samples, metric.error_threshold_pct, covered)
    summary = LogSummary(rows=analysis.rows, minutes_covered=analysis.minutes_covered, period_minutes=microseconds(start, end) // 60_000_000,
                         downtime_minutes=analysis.downtime_minutes, zero_request_minutes=analysis.zero_request_minutes,
                         below_threshold_error_minutes=analysis.below_threshold_error_minutes,
                         excluded_error_minutes=analysis.excluded_error_minutes, covered_experiences=list(covered), threshold_pct=metric.error_threshold_pct)
    basis = f"Uploaded request log: minutes where more than {format(Decimal(metric.error_threshold_pct).normalize(), 'f')}% of covered-experience requests failed"
    return analysis, summary, basis


def what_if_from_log(s: Scenario, csv_text: str) -> dict:
    analysis, summary, basis = measure_upload(s.terms, s.period, csv_text)
    period_minutes = summary.period_minutes
    copy = s.model_copy(deep=True)
    public = [i for i in copy.incidents if i.evidence_kind == "public_timeline"]
    # A full-period customer log answers every public incident in the period; identical intervals merge, so nothing is double counted.
    copy.incidents = public + [Incident(id=f"UPLOADED-LOG-{k + 1}", title="Uploaded request log", service=s.account.service,
                                        intervals=analysis.intervals, interval_basis=basis, severity="major_outage", evidence_kind="customer_metrics",
                                        metric_verified=True, related_incident=p.id if p else None, log_summary=summary, simulated=False,
                                        notes="Uploaded for a what-if. Not saved.") for k, p in enumerate(public or [None])]
    copy.account.history_complete = analysis.minutes_covered >= period_minutes
    decision = evaluate(copy, s.as_of)
    return {"what_if": True, "note": "Priced by the engine from the uploaded log. Nothing was saved: the stored case, claim, and approvals are unchanged.",
            "analysis": {**summary.model_dump(mode="json"),
                         "intervals": [{"start": i.start.isoformat(), "end": i.end.isoformat()} for i in analysis.intervals]},
            "decision": decision.model_dump(mode="json")}
