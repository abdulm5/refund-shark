"""Watch vendor status pages and turn each new outage into alerts for the customers it could owe.

A poll records every open incident once. Customers matched as affected or possibly affected get a
"ticket_now" alert while the incident is open (Atlassian's credit needs a support request during it),
and a "file_by" alert with the claim deadline once it resolves. An unreachable feed resolves nothing.
Drills inject a clearly simulated incident through the same path, for demos without a real outage.
"""
import json
import threading
from datetime import datetime, timezone

import httpx

from shark.engine.decision import deadline_bounds
from shark.engine.intervals import month_bounds
from shark.models import Customer, Scenario
from shark.storage import Store

from .live import LIVE_FEEDS, http_get, incident_action, open_incidents
from .matching import load_directory
from .statuspage import parse_time

STAMP = "%b %d, %Y %H:%M UTC"


class Watcher:
    def __init__(self, store: Store, scenarios: list[Scenario], customers: list[Customer] | None = None, get=http_get, feeds=LIVE_FEEDS):
        self.store, self.scenarios, self.get, self.feeds = store, scenarios, get, feeds
        self.customers = load_directory() if customers is None else customers
        self.drills: dict[str, list[dict]] = {}
        self.lock = threading.Lock()

    def deadline_text(self, vendor: str, product: str, incident: dict, now: datetime) -> str:
        agreement = next((s for s in self.scenarios if s.vendor == vendor and s.product == product), None)
        started = parse_time(incident["started_at"]) if incident.get("started_at") else now
        if agreement is None or agreement.terms.measurement_period.value != "calendar_month":
            return "the deadline in the agreement"  # only calendar-month windows are computed
        rule = agreement.terms.claim_window.value
        bounds = deadline_bounds(month_bounds(started.strftime("%Y-%m"))[1], rule) if rule else None
        if not bounds:
            return "the deadline in the agreement"
        earliest, latest = bounds
        return f"{earliest:{STAMP}}" + (" (earliest reading; the clause names no time zone)" if earliest != latest else "")

    def poll(self, now: datetime | None = None) -> dict:
        now = now or datetime.now(timezone.utc)
        stamp = now.isoformat()
        report = {"at": stamp, "feeds": [], "new_alerts": 0, "detected": 0, "resolved": 0}
        with self.lock:
            for vendor, product, host in self.feeds:
                try:
                    listed, ok = list(open_incidents(self.get(f"https://{host}/api/v2/incidents/unresolved.json", timeout=5))), True
                except (httpx.HTTPError, OSError, ValueError):
                    listed, ok = [], False
                listed += self.drills.get(host, [])
                seen = set()
                for incident in listed:
                    key, simulated = f"{host}:{incident['id']}", bool(incident.get("simulated"))
                    seen.add(key)
                    new = self.store.watched(key) is None
                    self.store.upsert_watched(key, host, vendor, product, incident, simulated, stamp)
                    matches, created = self.alert(key, vendor, product, incident, simulated, self.customers, stamp)
                    report["new_alerts"] += created
                    if new:
                        report["detected"] += 1
                        self.store.event("watch", f"{vendor} {product} incident detected" + (" (simulated drill)" if simulated else ""),
                                         f"{incident.get('name')}: {created} customer{'' if created == 1 else 's'} alerted, "
                                         f"{len(matches) - created} not affected.")
                for row in self.store.open_watched(host):
                    # Only a feed that answered can show an incident has ended; drills can always end.
                    if row["key"] in seen or not (ok or row["simulated"]):
                        continue
                    self.store.resolve_watched(row["key"], stamp)
                    report["resolved"] += 1
                    incident = json.loads(row["payload"])
                    deadline = self.deadline_text(vendor, product, incident, now)
                    for recipient in self.store.alert_recipients(row["key"]):
                        report["new_alerts"] += self.store.add_alert(
                            row["key"], recipient["customer_id"], recipient["customer"], "file_by", recipient["match_status"],
                            f"{vendor} {product} incident resolved: check your request logs",
                            f"If your logs show Downtime Minutes, file a compensation request by {deadline}.", None, bool(row["simulated"]), stamp)
                    self.store.event("watch", f"{vendor} {product} incident resolved" + (" (simulated drill)" if row["simulated"] else ""),
                                     f"{row['name']}: follow-ups sent with the filing deadline.")
                report["feeds"].append({"vendor": vendor, "product": product, "host": host, "ok": ok, "open": len(listed)})
            self.store.set_state("last_poll", report)
        return report

    def alert(self, key: str, vendor: str, product: str, incident: dict, simulated: bool, customers: list[Customer], stamp: str) -> tuple[list[dict], int]:
        """Ticket alerts for the customers an open incident could owe; the alerts table makes this idempotent."""
        matches, action, _ = incident_action(incident, vendor, product, customers, self.scenarios)
        created = 0
        for ticket in (action or {}).get("tickets", []):
            match = next(m for m in matches if m["customer_id"] == ticket["customer_id"])
            created += self.store.add_alert(key, ticket["customer_id"], ticket["customer"], "ticket_now", match["status"],
                                            f"{vendor} {product}: open a support ticket now",
                                            f"{match['reason']} The agreement asks to “{action['requirement_quote']}”.", ticket["text"], simulated, stamp)
        return matches, created

    def add_customers(self, customers: list[Customer], now: datetime | None = None) -> int:
        """Add (or replace) customers, and alert them about incidents that are already open."""
        stamp = (now or datetime.now(timezone.utc)).isoformat()
        created = 0
        with self.lock:
            ids = {c.customer_id for c in customers}
            self.customers = [c for c in self.customers if c.customer_id not in ids] + customers
            for vendor, product, host in self.feeds:
                for row in self.store.open_watched(host):
                    created += self.alert(row["key"], vendor, product, json.loads(row["payload"]), bool(row["simulated"]), customers, stamp)[1]
        return created

    def remove_customer(self, customer_id: str):
        with self.lock:
            self.customers = [c for c in self.customers if c.customer_id != customer_id]

    def start_drill(self, now: datetime | None = None) -> dict:
        now = now or datetime.now(timezone.utc)
        host = next(h for v, p, h in self.feeds if (v, p) == ("Atlassian", "Jira"))
        self.drills[host] = [{"id": f"DRILL-{now:%Y%m%d%H%M%S}", "name": "Simulated drill: Jira errors in US East", "status": "investigating",
                              "impact": "major", "started_at": now.isoformat(), "shortlink": None, "components": [{"name": "Viewing content"}],
                              "incident_updates": [], "simulated": True}]
        return self.poll(now)

    def end_drill(self, now: datetime | None = None) -> dict:
        self.drills.clear()
        return self.poll(now)
