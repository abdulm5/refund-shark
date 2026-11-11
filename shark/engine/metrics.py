"""Turn per-minute request logs into Downtime Minutes, as a request-error-rate SLA defines them.

Atlassian: a Downtime Minute "occurs when the Error Rate in a given minute is greater than 5%", where
the Error Rate is the share of the customer's requests to Covered Experiences that failed in that
minute, and a minute with no requests has an Error Rate of 0%. Requests to anything else (APIs,
integrations, mobile) are not covered and never count.

Log format (CSV, optionally gzipped): minute,experience,requests,errors with minute in ISO 8601 UTC.
"""
import csv
import gzip
import hashlib
import io
from collections import defaultdict
from functools import lru_cache
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from shark.models import Interval, LogSummary, Scenario

ROOT = Path(__file__).resolve().parents[2]

MINUTE = timedelta(minutes=1)


@dataclass(frozen=True)
class Sample:
    minute: datetime
    experience: str
    requests: int
    errors: int


@dataclass
class LogAnalysis:
    intervals: list[Interval]
    downtime_minutes: int
    minutes_covered: int
    zero_request_minutes: int
    below_threshold_error_minutes: int  # covered errors that stayed at or under the threshold
    excluded_error_minutes: dict[str, int] = field(default_factory=dict)  # minutes with errors on uncovered experiences
    rows: int = 0


def downtime_from_samples(samples, threshold_pct: Decimal, covered: list[str]) -> LogAnalysis:
    covered_set = set(covered)
    totals: dict[datetime, list[int]] = defaultdict(lambda: [0, 0])
    seen, excluded, rows, pairs = set(), defaultdict(set), 0, set()
    for s in samples:
        rows += 1
        if (s.minute, s.experience) in pairs:
            raise ValueError(f"{s.minute.isoformat()} appears more than once for {s.experience}; give one row per minute and experience.")
        pairs.add((s.minute, s.experience))
        if s.minute.tzinfo is None or s.minute.second or s.minute.microsecond:
            raise ValueError(f"Log minutes must be whole, time-zone-aware minutes: {s.minute.isoformat()}")
        if s.requests < 0 or not 0 <= s.errors <= s.requests:
            raise ValueError(f"Impossible counts at {s.minute.isoformat()} for {s.experience}")
        seen.add(s.minute)
        if s.experience in covered_set:
            totals[s.minute][0] += s.requests
            totals[s.minute][1] += s.errors
        elif s.errors:
            excluded[s.experience].add(s.minute)
    down, quiet, below = [], 0, 0
    for at in sorted(seen):
        requests, errors = totals[at] if at in totals else (0, 0)
        if requests == 0:
            quiet += 1  # no requests: the Error Rate is 0%
        elif errors * 100 > threshold_pct * requests:  # strictly greater than the threshold, without rounding
            down.append(at)
        elif errors:
            below += 1
    intervals: list[Interval] = []
    for at in down:
        if intervals and intervals[-1].end == at:
            intervals[-1] = Interval(start=intervals[-1].start, end=at + MINUTE)
        else:
            intervals.append(Interval(start=at, end=at + MINUTE))
    return LogAnalysis(intervals=intervals, downtime_minutes=len(down), minutes_covered=len(seen), zero_request_minutes=quiet,
                       below_threshold_error_minutes=below, excluded_error_minutes={k: len(v) for k, v in sorted(excluded.items())}, rows=rows)


def parse_log(text: str) -> list[Sample]:
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    fields = [f.strip().lower() for f in reader.fieldnames or []]
    if fields != ["minute", "experience", "requests", "errors"]:
        raise ValueError("Expected a CSV with the columns minute,experience,requests,errors (one row per minute and experience).")
    reader.fieldnames = fields
    samples = []
    try:
        for row in reader:
            try:
                minute = datetime.fromisoformat(row["minute"].strip().replace("Z", "+00:00"))
                if minute.tzinfo is None:
                    raise ValueError("no time zone")
                samples.append(Sample(minute=minute, experience=row["experience"].strip(), requests=int(row["requests"]), errors=int(row["errors"])))
            except (TypeError, ValueError, AttributeError, KeyError):
                raise ValueError(f"Line {reader.line_num} is not a valid row: the minute needs ISO 8601 with a time zone "
                                 "(for example 2026-09-03T15:04Z), then experience, requests, errors.") from None
    except csv.Error as error:
        # The reader fails before counting the line it could not read.
        raise ValueError(f"Line {reader.line_num + 1} could not be read as CSV: {error}.") from None
    return samples


def read_log(path: Path) -> list[Sample]:
    raw = path.read_bytes()
    return parse_log(gzip.decompress(raw).decode() if path.suffix == ".gz" else raw.decode())


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@lru_cache(maxsize=32)
def analyze_log(path: Path, sha256: str, period: str, threshold_pct: Decimal, covered: tuple[str, ...] | None):
    """Parse and analyze a pinned log once per (recorded hash, period, rule).

    A file is analyzed only if its bytes match the recorded hash; a log edited while the process runs is
    not re-read until restart, so a cached result always belongs to the recorded file.
    """
    from .intervals import month_bounds
    if file_sha256(path) != sha256:
        raise ValueError(f"The request log changed since it was recorded: {path}")
    start, end = month_bounds(period)
    samples = [s for s in read_log(path) if start <= s.minute < end]
    experiences = list(covered) if covered else sorted({s.experience for s in samples})
    analysis = downtime_from_samples(samples, threshold_pct, experiences)
    down = {i.start + MINUTE * k for i in analysis.intervals for k in range(int((i.end - i.start) / MINUTE))}
    errored = sorted({s.experience for s in samples if s.minute in down and s.errors and s.experience in experiences})
    return analysis, experiences, errored


def apply_logs(scenario: Scenario, root: Path = ROOT) -> Scenario:
    """Replace each logged record's intervals with Downtime Minutes computed from its pinned log."""
    from .intervals import month_bounds, microseconds
    metric = scenario.terms.downtime_definition.value
    if metric is None or metric.kind != "request_error_rate" or metric.error_threshold_pct is None:
        # The log cannot be measured against this agreement's metric: never let it read as zero downtime.
        for record in scenario.incidents:
            if record.log is not None:
                record.intervals, record.metric_verified = [], False
                record.notes = (record.notes + " The agreement's downtime metric is not a per-minute request error rate, so this log cannot be measured against it.").strip()
        return scenario
    start, end = month_bounds(scenario.period)
    for record in scenario.incidents:
        if record.log is None:
            continue
        try:
            analysis, experiences, errored = analyze_log(root / record.log.path, record.log.sha256, scenario.period, metric.error_threshold_pct,
                                                         tuple(metric.covered_experiences) or None)
        except FileNotFoundError:
            # An uploaded log that is no longer on disk cannot be re-measured: review, never zero downtime.
            record.intervals, record.metric_verified, record.log_summary = [], False, None
            record.notes = (record.notes + f" The request log file is missing: {record.log.path}.").strip()
            continue
        except ValueError as error:
            raise ValueError(f"{record.id}: {error}") from None
        record.intervals = [i.model_copy() for i in analysis.intervals]
        record.experiences = list(errored)
        record.log_summary = LogSummary(rows=analysis.rows, minutes_covered=analysis.minutes_covered,
                                        period_minutes=microseconds(start, end) // 60_000_000, downtime_minutes=analysis.downtime_minutes,
                                        zero_request_minutes=analysis.zero_request_minutes, below_threshold_error_minutes=analysis.below_threshold_error_minutes,
                                        excluded_error_minutes=analysis.excluded_error_minutes, covered_experiences=list(experiences),
                                        threshold_pct=metric.error_threshold_pct)
    return scenario
