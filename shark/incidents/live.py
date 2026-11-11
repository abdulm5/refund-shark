"""Open incidents right now, and what each agreement asks the customer to do while they last.

Atlassian's compensation procedure asks for "a technical support request during the incident";
after the incident ends, that prerequisite can no longer be met. The ticket text is rendered by code.
"""
from datetime import datetime, timedelta, timezone

import httpx

from shark.models import Customer, Scenario

from shark.spend.constants import UNKNOWN_PLAN

from .matching import load_directory, match_customers
from .statuspage import is_statuspage_host, parse_time

LIVE_FEEDS = [("Atlassian", "Jira", "jira-software.status.atlassian.com"), ("GitHub", "Enterprise Cloud", "www.githubstatus.com"),
              ("Cloudflare", "Business", "www.cloudflarestatus.com")]


def http_get(url: str, timeout: float) -> dict:
    response = httpx.get(url, timeout=timeout, headers={"User-Agent": "RefundSharkResearch/0.1"})
    response.raise_for_status()
    return response.json()


def requirement_quote(s: Scenario) -> str | None:
    fragments = [f.strip() for f in (s.terms.prerequisites.quote or "").split("[…]")]
    return next((f for f in fragments if "during the incident" in f), None)


def ticket_text(product: str, plan: str, account_reference: str | None, incident: dict) -> str:
    started = parse_time(incident["started_at"]).strftime("%Y-%m-%d %H:%M UTC") if incident.get("started_at") else "the start of the incident"
    link = f" ({incident['shortlink']})" if incident.get("shortlink") else ""
    named = product if plan == UNKNOWN_PLAN else f"{product} {plan}"  # never put "unknown" in a ticket to the vendor
    return (f"Subject: {named} service disruption ({incident['id']})\n\n"
            f"We are seeing errors in {product} for account {account_reference or '[account]'} since {started}, matching your open incident "
            f"\"{incident['name']}\"{link}.\nPlease record this technical support request against the incident. If a Service Level Agreement "
            "credit applies, we will follow up with our request logs.\n")


ALERTABLE = {"affected", "possibly_affected"}


def open_incidents(payload) -> list[dict]:
    """The incidents in a Statuspage response, or ValueError: a malformed answer must never read as "nothing open"."""
    incidents = payload.get("incidents") if isinstance(payload, dict) else None
    if not isinstance(incidents, list) or any(not isinstance(i, dict) or not i.get("id") for i in incidents):
        raise ValueError("Unexpected status page response")
    return incidents


def incident_action(incident: dict, vendor: str, product: str, customers: list[Customer], scenarios: list[Scenario]) -> tuple[list[dict], dict | None, str]:
    """Match the workspace's customers, then prepare tickets only for those who could be owed a credit.

    Tickets quote the agreement, so only agreements quoted from a real source can produce them; the demo's
    authored boundary agreements never stand in for a vendor's terms.
    """
    matches = match_customers(incident, vendor, product, customers, scenarios)
    agreements = [s for s in scenarios if s.vendor == vendor and s.product == product and s.source_refs]
    if not agreements:
        return matches, None, "No agreement quoted from a real source is on file for this vendor (the demo's terms for it are authored), so no ticket is prepared."
    quoted = next((q for q in map(requirement_quote, agreements) if q), None)
    if not quoted:
        return matches, None, "The agreement on file does not ask for a ticket during the incident."
    tickets = [{"customer": m["customer"], "customer_id": m["customer_id"], "status": m["status"],
                "text": ticket_text(product, m["plan"], m["account_reference"], incident)} for m in matches if m["status"] in ALERTABLE]
    if not tickets:
        return matches, None, "No customer in this workspace could be owed a credit for this incident."
    return matches, {"requirement_quote": quoted, "source_label": agreements[0].source_label, "tickets": tickets}, ""


def live_status(scenarios: list[Scenario], get=http_get, now: datetime | None = None, preview: bool = False,
                customers: list[Customer] | None = None) -> dict:
    """`preview` adds one clearly simulated Jira incident, so the ticket flow can be shown without a real outage."""
    now = now or datetime.now(timezone.utc)
    customers = load_directory() if customers is None else customers
    feeds = []
    for vendor, product, host in LIVE_FEEDS:
        assert is_statuspage_host(host)
        feed = {"vendor": vendor, "product": product, "host": host, "status_url": f"https://{host}"}
        try:
            listed_now = open_incidents(get(f"https://{host}/api/v2/incidents/unresolved.json", timeout=5))
        except (httpx.HTTPError, OSError, ValueError):
            feeds.append({**feed, "ok": False, "incidents": [], "message": "Status feed unavailable; open incidents are unknown."})
            continue
        incidents = []
        listed = list(listed_now)
        if preview and product == "Jira":
            listed.append({"id": "PREVIEW", "name": "Simulated preview: Jira errors in US East", "impact": "major", "status": "investigating",
                           "started_at": (now - timedelta(minutes=25)).isoformat(), "shortlink": None, "components": [{"name": "Viewing content"}],
                           "simulated": True})
        for incident in listed:
            matches, action, note = incident_action(incident, vendor, product, customers, scenarios)
            incidents.append({"action_note": note, "id": incident["id"], "name": incident.get("name", "Open incident"), "impact": incident.get("impact"),
                              "status": incident.get("status"), "started_at": incident.get("started_at"), "shortlink": incident.get("shortlink"),
                              "components": [c.get("name") for c in incident.get("components") or []], "matches": matches, "action": action,
                              "simulated": bool(incident.get("simulated"))})
        feeds.append({**feed, "ok": True, "incidents": incidents,
                      "message": f"{len(incidents)} open incident{'' if len(incidents) == 1 else 's'}" if incidents else "No open incidents"})
    return {"checked_at": now.isoformat(), "feeds": feeds, "preview": preview,
            "open_incidents": sum(len(f["incidents"]) for f in feeds)}
