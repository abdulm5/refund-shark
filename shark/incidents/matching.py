"""Narrow an outage to the customers it could owe a credit: plan, region, and covered features.

Status pages describe incidents for everyone; a credit exists only where the customer's plan has an
SLA, the incident reached the customer's site region, and a covered experience was affected. When the
incident does not say, the customer is "possibly affected", never silently included or dropped.
"""
import json
import re

from shark.models import Customer, Scenario
from shark.spend.constants import UNKNOWN_PLAN

from ..pipeline import ROOT

REGIONS = {"US East": r"\bus[- ]?east\b|\beast(?:ern)? us\b", "US West": r"\bus[- ]?west\b|\bwest(?:ern)? us\b",
           "EU": r"\beu\b|\beu[- ](?:central|west)\b|\beurope(?:an)?\b", "APAC": r"\bapac\b|\basia[- ]pacific\b|\baustralia\b|\bsingapore\b|\bjapan\b"}
# Public status components that correspond to an agreement's covered experiences, and ones that plainly do not.
# A component in neither list (authentication, for example) leaves the customer "possibly affected".
COVERED_COMPONENTS = {("Atlassian", "Jira"): {"Create and edit", "Viewing content"}}
NON_COVERED_COMPONENTS = {("Atlassian", "Jira"): {"Search", "Notifications", "Administration", "Marketplace", "Mobile", "Purchasing & Licensing",
                                                  "Signup", "Automation for Jira"}}


def load_directory() -> list[Customer]:
    return [Customer.model_validate(c) for c in json.loads((ROOT / "data/fixtures/customers.json").read_text())["customers"]]


def incident_scope(incident: dict) -> dict:
    """Regions named in the incident's title or update text, and the components it touched."""
    updates = incident.get("incident_updates") or incident.get("updates") or []
    text = " ".join([incident.get("name", "")] + [u.get("body") or "" for u in updates]).lower()
    regions = [name for name, pattern in REGIONS.items() if re.search(pattern, text)]
    components = {c.get("name") for c in incident.get("components") or [] if c.get("name")}
    components |= {c.get("name") for u in updates for c in u.get("components") or [] if c.get("name")}
    return {"regions": regions, "components": sorted(components)}


def covered_plans(vendor: str, product: str, scenarios: list[Scenario]) -> set[str]:
    catalog = json.loads((ROOT / "docs/research/sla-catalog.json").read_text())["sources"]
    # Cases people start carry a plan name they typed; only the workspace's own agreements and the catalog decide which plans have an SLA.
    plans = {s.terms.plan.value for s in scenarios if s.vendor == vendor and s.product == product and s.terms.plan.value and s.role != "user_case"}
    for source in catalog:
        if source["vendor"] == vendor:
            plans |= set(source.get("commitment_pct_by_plan", {}))
    return plans


def match_customers(incident: dict, vendor: str, product: str, customers: list[Customer], scenarios: list[Scenario]) -> list[dict]:
    scope = incident_scope(incident)
    plans = covered_plans(vendor, product, scenarios)
    covered, outside = COVERED_COMPONENTS.get((vendor, product), set()), NON_COVERED_COMPONENTS.get((vendor, product), set())
    components = set(scope["components"])
    hit_covered = True if components & covered else False if components and components <= outside else None
    matches = []
    for customer in customers:
        for sub in customer.subscriptions:
            if sub.vendor != vendor or (sub.product is not None and sub.product != product):
                continue
            unknown = sub.plan == UNKNOWN_PLAN  # spend shows the vendor, not the plan: never "affected", never dropped
            if not unknown and sub.plan not in plans:
                status, reason = "no_sla", f"The {sub.plan} plan has no uptime commitment, so no credit is possible."
            elif hit_covered is False:
                status, reason = "outside_covered", f"Only {', '.join(scope['components'])} affected; none is a covered experience."
            elif scope["regions"] and sub.site_region and sub.site_region not in scope["regions"]:
                status, reason = "other_region", f"Site in {sub.site_region}; the incident names {', '.join(scope['regions'])}."
            elif components and hit_covered is None:
                status, reason = "possibly_affected", (f"{', '.join(sorted(components))} may or may not break covered experiences."
                                                       + (f" Site in {sub.site_region}." if sub.site_region else "") + " Confirm with request logs.")
            elif scope["regions"] and sub.site_region:
                status, reason = "affected", f"{sub.plan} plan with an SLA, site in {sub.site_region}, covered features affected."
            else:
                status, reason = "possibly_affected", ("The incident does not name a region." if not scope["regions"]
                                                       else "This customer's site region is unknown.") + " Confirm with request logs."
            if unknown and status in {"affected", "possibly_affected"}:
                status = "possibly_affected"
                reason = (f"Spend shows payments to {vendor}, but not the {'plan' if sub.product else 'product or plan'}, so an SLA isn't confirmed. "
                          + (reason if not reason.startswith(f"{sub.plan} plan") else "Covered features affected.").replace(" Confirm with request logs.", "")
                          + " Confirm the plan and check request logs.")
            matches.append({"customer_id": customer.customer_id, "customer": customer.display_name, "contact": customer.contact,
                            "plan": sub.plan, "site_region": sub.site_region, "account_reference": sub.account_reference,
                            "status": status, "reason": reason, "simulated": customer.simulated})
    order = ["affected", "possibly_affected", "other_region", "outside_covered", "no_sla"]
    return sorted(matches, key=lambda m: (order.index(m["status"]), m["customer"]))
