import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx

from shark.models import Incident, Interval


def parse_time(value: str):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Incident timestamp lacks a timezone")
    return result


SEVERITY = ["operational", "degraded_performance", "partial_outage", "major_outage"]


def normalize_incident(record: dict, component_ids: set[str], service: str) -> Incident:
    """Rebuild an incident's windows from its historical component updates, in time order.

    The windows cover the worst status any in-scope component reached; a later return to an
    active status after monitoring opens a new window. Current component status is never used.
    """
    updates = sorted(record.get("incident_updates", record.get("updates", [])), key=lambda u: parse_time(u.get("display_at") or u["created_at"]))
    seen, timeline, statuses = set(), [], {}
    for update in updates:
        if update.get("id") in seen:
            continue
        if update.get("id"):
            seen.add(update["id"])
        for component in update.get("components") or []:
            cid = component.get("code") or component.get("id")
            if cid in component_ids and "new_status" in component:
                statuses[cid] = component["new_status"]
        recovering = update["status"] in {"monitoring", "resolved", "postmortem"}
        level = 0 if recovering else max((SEVERITY.index(v) for v in statuses.values() if v in SEVERITY), default=0)
        timeline.append((parse_time(update.get("display_at") or update["created_at"]), level))
        if recovering:
            statuses = {}
    worst = max((level for _, level in timeline), default=0)
    intervals, active_start = [], None
    for at, level in timeline:
        if level == worst and worst and active_start is None:
            active_start = at
        elif level != worst and active_start is not None:
            if at > active_start:
                intervals.append(Interval(start=active_start, end=at))
            active_start = None
    severity = SEVERITY[worst]
    started = record.get("started_at") or record.get("created_at")
    started_at = parse_time(started) if started else (timeline[0][0] if timeline else None)
    # An unresolved endpoint cannot safely be treated as an ended interval.
    return Incident(id=record["id"], title=record.get("name", "Public vendor incident"), service=service, intervals=intervals,
                    interval_basis=f"Public status updates: in-scope components in {severity.replace('_', ' ')}", severity=severity,
                    source_url=record.get("shortlink") or record.get("source_url"), evidence_kind="public_timeline", metric_verified=False, simulated=False,
                    started_at=started_at,
                    notes="Estimate from historical component updates; public feeds do not prove complete customer-period coverage." + (" Open interval needs review." if active_start else ""))


STATUSPAGE_HOSTS = {"githubstatus.com", "www.githubstatus.com", "cloudflarestatus.com", "www.cloudflarestatus.com", "status.bitbucket.org"}


def is_statuspage_host(host: str) -> bool:
    """Exact vendor hosts, or a subdomain of the Atlassian and Statuspage roots: never a bare suffix match."""
    return host in STATUSPAGE_HOSTS or host.endswith((".status.atlassian.com", ".statuspage.io"))


def fetch_feed(host: str, cache_dir: Path) -> dict:
    parsed = urlparse("https://" + host)
    if parsed.hostname != host or not is_statuspage_host(host):
        raise ValueError("Use a recognized public vendor Statuspage hostname")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    folder = cache_dir / host / stamp
    folder.mkdir(parents=True, exist_ok=True)
    counts = {}
    with httpx.Client(timeout=30) as client:
        for endpoint in ["incidents", "scheduled-maintenances"]:
            response = client.get(f"https://{host}/api/v2/{endpoint}.json")
            response.raise_for_status()
            payload = response.json()
            (folder / f"{endpoint}.json").write_text(json.dumps(payload, indent=2) + "\n")
            counts[endpoint] = len(payload.get("incidents", payload.get("scheduled_maintenances", [])))
    return {"host": host, "retrieved_at": stamp, "path": str(folder), "counts": counts, "complete_period_coverage": False}
