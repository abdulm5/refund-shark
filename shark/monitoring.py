"""Read-only Datadog count-metric connector. Missing buckets are never filled with zero."""
import csv
import io
import os
import threading
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Literal

import httpx
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from shark.recovery import EvidenceInput, add_evidence
from shark.storage import utcnow

SITES = {s: f"https://api.{s}" for s in ("datadoghq.com", "datadoghq.eu", "us3.datadoghq.com", "us5.datadoghq.com", "ap1.datadoghq.com", "ap2.datadoghq.com", "uk1.datadoghq.com")}
MINUTE = timedelta(minutes=1)


class MetricMapping(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    experience: str = Field(min_length=1, max_length=120)
    requests_metric: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_.]{0,199}$")
    errors_metric: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_.]{0,199}$")
    scope: str = Field(min_length=3, max_length=400, pattern=r"^[a-zA-Z0-9_:.,/*\-]+$")


class MonitoringConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    site: str = "datadoghq.com"
    mappings: list[MetricMapping] = Field(min_length=1, max_length=8)
    confirmed_count_metrics: Literal[True]
    automatic: bool = False

    @model_validator(mode="after")
    def validate_mapping(self):
        if self.site not in SITES:
            raise ValueError("Choose a supported Datadog site.")
        if len({m.experience for m in self.mappings}) != len(self.mappings):
            raise ValueError("Map each covered experience only once.")
        if any(m.scope == "*" or ":" not in m.scope for m in self.mappings):
            raise ValueError("Scope every metric to this customer's account and service using tags.")
        return self


class SyncWindow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: AwareDatetime
    end: AwareDatetime

    @model_validator(mode="after")
    def bounds(self):
        if self.start.second or self.start.microsecond or self.end.second or self.end.microsecond:
            raise ValueError("Choose whole-minute boundaries.")
        if not MINUTE * 2 <= self.end - self.start <= timedelta(days=31):
            raise ValueError("Choose a range from two minutes to 31 days.")
        if self.end > datetime.now(timezone.utc).replace(second=0, microsecond=0) - MINUTE * 2:
            raise ValueError("End the range at least two minutes ago so in-flight buckets are not treated as complete.")
        return self


def credentials_ready():
    return bool(os.getenv("SHARK_DATADOG_API_KEY") and os.getenv("SHARK_DATADOG_APP_KEY"))


def validate_for_vendor(store, vendor_id, config):
    vendor = store.record("vendor", vendor_id)
    read = store.get_read((vendor or {}).get("read_id"))
    if not vendor or not read:
        raise ValueError("Save the vendor and link its agreement before configuring monitoring.")
    metric = read["terms"]["downtime_definition"]["value"]
    if not metric or metric["kind"] != "request_error_rate" or metric["interval_seconds"] != 60:
        raise ValueError("This connector needs an agreement measured by per-minute request error rates.")
    covered = metric["covered_experiences"]
    if not covered or set(covered) != {m.experience for m in config.mappings}:
        raise ValueError("Map every covered experience exactly once: " + ", ".join(covered))
    return vendor, read


def monitoring_status(store, vendor_id):
    record = store.record("monitoring", vendor_id)
    return {"config": record, "credentials_configured": credentials_ready(), "sites": list(SITES),
            "automatic_interval_seconds": monitoring_interval(), "status": store.record("monitoring_status", vendor_id)}


def save_monitoring(store, vendor_id, config):
    _, read = validate_for_vendor(store, vendor_id, config)
    store.save_record("monitoring", {"id": vendor_id, **config.model_dump(mode="json"), "read_id": read["id"], "updated_at": utcnow()})
    previous = store.record("monitoring_status", vendor_id)
    if previous:
        store.save_record("monitoring_status", {"id": vendor_id, "state": "configured", "last_attempt": previous.get("last_attempt"),
            "last_success": previous.get("last_success"), "error": None, "evidence_ids": previous.get("evidence_ids", [])})
    return monitoring_status(store, vendor_id)


def query_payload(config, start, end):
    queries = []
    for index, mapping in enumerate(config.mappings):
        for kind in ("requests", "errors"):
            queries.append({"name": f"m{index}_{kind}", "data_source": "metrics",
                "query": f"sum:{getattr(mapping, kind + '_metric')}{{{mapping.scope}}}.as_count().rollup(sum,60).fill(null)"})
    return {"data": {"type": "timeseries_request", "attributes": {"from": int(start.timestamp() * 1000),
            "to": int(end.timestamp() * 1000), "interval": 60_000, "queries": queries}}}


def count(value):
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number < 0 or number != number.to_integral_value() or number > 10**15:
            raise ValueError()
        return int(number)
    except (InvalidOperation, ValueError):
        raise ValueError("Datadog must return nonnegative integer counts, not rates, gauges, or interpolated values.") from None


def parse_response(payload, config, start, end):
    if not isinstance(payload, dict):
        raise ValueError("Datadog returned an unexpected response shape. No evidence was imported.")
    try:
        if payload.get("errors") or payload["data"]["type"] != "timeseries_response":
            raise ValueError("Datadog reported a query error.")
        a = payload["data"]["attributes"]
        times, series, values = a["times"], a["series"], a["values"]
        if not times or len(times) < 2:
            raise ValueError("Datadog returned no usable one-minute series. Check metric names, retention, scope, and time range.")
        if any(type(t) is not int or t % 60_000 for t in times) or any(b - a != 60_000 for a, b in zip(times, times[1:])):
            raise ValueError("Datadog returned coarse or misaligned buckets. One-minute data is required; try a shorter or more recent range.")
        if len(series) != len(values):
            raise ValueError("Datadog returned inconsistent series.")
        indexed = {}
        for meta, points in zip(series, values):
            idx = meta["query_index"]
            if type(idx) is not int or idx < 0 or idx >= len(config.mappings) * 2 or idx in indexed or len(points) != len(times):
                raise ValueError("Each mapped metric must return exactly one ungrouped series.")
            indexed[idx] = points
        if set(indexed) != set(range(len(config.mappings) * 2)):
            raise ValueError("One or more mapped metrics returned no series. Missing metrics cannot be assumed to be zero.")
        rows, skipped = [], 0
        for p, timestamp in enumerate(times):
            at = datetime.fromtimestamp(timestamp / 1000, timezone.utc)
            if not start <= at < end:
                continue
            # Require every experience on both metrics for a minute; partial requests could distort the error rate.
            if any(points[p] is None for points in indexed.values()):
                skipped += 1
                continue
            for i, m in enumerate(config.mappings):
                requests, errors = count(indexed[i * 2][p]), count(indexed[i * 2 + 1][p])
                if errors > requests:
                    raise ValueError("An error count exceeds requests. Check account filters and metric mappings.")
                rows.append((at, m.experience, requests, errors))
        return rows, skipped
    except (KeyError, TypeError, OverflowError, IndexError):
        raise ValueError("Datadog returned an unexpected response shape. No evidence was imported.") from None


class Monitoring:
    def __init__(self, store, sample_text):
        self.store, self.sample_text = store, sample_text
        self.lock = threading.Lock()

    def sync(self, vendor_id, window):
        if not self.lock.acquire(blocking=False):
            raise ValueError("A monitoring sync is already running. Wait for it to finish.")
        previous = self.store.record("monitoring_status", vendor_id) or {}
        status = {"id": vendor_id, "state": "syncing", "last_attempt": utcnow(), "last_success": previous.get("last_success"),
                  "error": None, "evidence_ids": previous.get("evidence_ids", [])}
        try:
            saved = self.store.record("monitoring", vendor_id)
            if not saved:
                raise ValueError("Save the Datadog mapping first.")
            config = MonitoringConfig.model_validate({k: saved[k] for k in MonitoringConfig.model_fields})
            validate_for_vendor(self.store, vendor_id, config)
            if not credentials_ready():
                raise ValueError("Configure SHARK_DATADOG_API_KEY and SHARK_DATADOG_APP_KEY on the server first.")
            self.store.save_record("monitoring_status", status)
            headers = {"DD-API-KEY": os.environ["SHARK_DATADOG_API_KEY"], "DD-APPLICATION-KEY": os.environ["SHARK_DATADOG_APP_KEY"]}
            rows, skipped, start = [], 0, window.start.astimezone(timezone.utc)
            with httpx.Client(timeout=20, follow_redirects=False) as client:
                while start < window.end:
                    end = min(start + timedelta(hours=6), window.end)
                    # Avoid a final single-point request.
                    if window.end - end == MINUTE:
                        end = window.end
                    response = client.post(SITES[config.site] + "/api/v2/query/timeseries", headers=headers, json=query_payload(config, start, end))
                    if response.status_code != 200:
                        raise ValueError(f"Datadog returned HTTP {response.status_code}. Check credentials, timeseries_query permission, and rate limits. No automatic retry was made.")
                    try:
                        payload = response.json()
                    except ValueError:
                        raise ValueError("Datadog returned invalid JSON. No evidence was imported.") from None
                    chunk, gaps = parse_response(payload, config, start, end)
                    rows.extend(chunk); skipped += gaps; start = end
            if not rows:
                raise ValueError("No complete minute buckets were returned. Missing data remains unknown.")
            evidence_ids = []
            for month in sorted({r[0].strftime("%Y-%m") for r in rows}):
                out = io.StringIO(); writer = csv.writer(out)
                writer.writerow(["minute", "experience", "requests", "errors"])
                for at, experience, requests, errors in rows:
                    if at.strftime("%Y-%m") == month:
                        writer.writerow([at.isoformat(), experience, requests, errors])
                item = add_evidence(self.store, vendor_id, EvidenceInput(kind="logs", period=month,
                    label=f"Datadog {window.start.isoformat()} – {window.end.isoformat()}", csv=out.getvalue()), "datadog", self.sample_text)
                item["monitoring"] = {"site": config.site, "mappings": [m.model_dump() for m in config.mappings],
                    "start": window.start.isoformat(), "end": window.end.isoformat(), "skipped_minutes": skipped,
                    "note": "Mapped customer count metrics. Missing buckets are omitted, never filled with zero."}
                self.store.save_record("evidence", item)
                evidence_ids.append(item["id"])
            status.update(state="success", last_success=utcnow(), evidence_ids=evidence_ids, rows=len(rows), skipped_minutes=skipped,
                          start=window.start.isoformat(), end=window.end.isoformat())
        except (httpx.HTTPError, OSError) as error:
            status.update(state="error", error="Could not reach Datadog. Check connectivity and retry manually; credentials and response bodies are not logged.")
        except ValueError as error:
            status.update(state="error", error=str(error))
        finally:
            self.store.save_record("monitoring_status", status)
            self.lock.release()
        return status

    def tick(self):
        end = datetime.now(timezone.utc).replace(second=0, microsecond=0) - MINUTE * 5
        for saved in self.store.records("monitoring"):
            if not saved["automatic"]:
                continue
            state = self.store.record("monitoring_status", saved["id"]) or {}
            # A failure pauses automatic ingestion until an operator retries or saves the mapping.
            if state.get("state") == "error":
                continue
            start = datetime.fromisoformat(state["end"]) if state.get("state") == "success" else end - timedelta(hours=1)
            start = max(start, end - timedelta(days=31))
            if end - start >= MINUTE * 2:
                self.sync(saved["id"], SyncWindow(start=start, end=end))


def monitoring_interval():
    try:
        value = int(os.getenv("SHARK_MONITOR_INTERVAL", "0"))
        return max(60, value) if value > 0 else 0
    except ValueError:
        return 0
