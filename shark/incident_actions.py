"""An operator's incident response, kept separate from the public status timeline."""
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field, AwareDatetime, model_validator

from shark.recovery import EvidenceInput, add_evidence
from shark.storage import utcnow

SUPPORT = {"Atlassian": "https://support.atlassian.com/contact/", "GitHub": "https://support.github.com/"}


class TicketCapture(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    reference: str = Field(min_length=1, max_length=200)
    url: str | None = Field(default=None, max_length=2000)
    opened_at: AwareDatetime
    incident_start: AwareDatetime | None = None
    incident_end: AwareDatetime | None = None
    confirmed: bool

    @model_validator(mode="after")
    def shape(self):
        if not self.confirmed:
            raise ValueError("Confirm that you actually opened this support ticket.")
        if self.incident_end and not self.incident_start:
            raise ValueError("Enter the customer impact start as well as its end.")
        if self.incident_start and self.incident_end and self.incident_start >= self.incident_end:
            raise ValueError("Customer impact must end after it starts.")
        return self


def alert_record(store, alert_id):
    # Direct lookup keeps older actionable records accessible beyond the list's display limit.
    with store.connect() as db:
        row = db.execute("SELECT * FROM alerts WHERE id=?", (alert_id,)).fetchone()
    if not row:
        raise ValueError("This incident alert no longer exists.")
    return dict(row)


def action_panel(store, watcher, alert_id):
    a = alert_record(store, alert_id)
    incident = store.watched(a["incident_key"])
    vendor = store.record("vendor", a["customer_id"])
    customer = next((c for c in watcher.customers if c.customer_id == a["customer_id"]), None)
    sub = next((s for s in customer.subscriptions if s.vendor == incident["vendor"] and (not s.product or s.product == incident["product"])), None) if customer else None
    saved = store.record("incident_action", f"action-{alert_id}")
    return {"alert_id": alert_id, "incident_key": a["incident_key"], "customer": a["customer"], "vendor": incident["vendor"],
        "product": incident["product"], "incident_name": incident["name"], "open": bool(incident["open"]),
        "simulated": bool(a["simulated"]), "owner": vendor["owner_name"] if vendor else customer.contact if customer else "Unassigned",
        "owner_email": vendor["owner_email"] if vendor else None, "account_reference": vendor["account_reference"] if vendor else sub.account_reference if sub else None,
        "plan": vendor["plan"] if vendor else sub.plan if sub else "unknown", "region": vendor["region"] if vendor else sub.site_region if sub else None,
        "vendor_id": vendor["id"] if vendor else None, "support_url": SUPPORT.get(incident["vendor"]),
        "requirement": a["detail"], "draft": a["ticket"] or "Use the support draft on the original outage alert.", "capture": saved,
        "steps": [{"label": "Review the affected subscription", "done": bool(vendor and vendor["confirmed"])},
                  {"label": "Open a technical support request", "done": bool(saved)},
                  {"label": "Confirm customer impact and ticket timing", "done": bool(saved and saved.get("ticket_during_incident") is True)},
                  {"label": "Collect logs for the monthly claim", "done": bool(vendor and any(e["kind"] == "logs" and e["vendor_id"] == vendor["id"] for e in store.records("evidence")))}]}


def capture_ticket(store, watcher, alert_id, form, sample_text):
    a = action_panel(store, watcher, alert_id)
    now = datetime.now(timezone.utc)
    if form.opened_at > now or (form.incident_end and form.incident_end > now):
        raise ValueError("A recorded ticket or completed impact window cannot be in the future.")
    # In-progress impact can be recorded, but stays unconfirmed until both boundaries are supplied.
    complete = bool(form.incident_start and form.incident_end)
    period = (form.incident_start or form.opened_at).strftime("%Y-%m")
    ticket = EvidenceInput(kind="ticket", period=period, label=f"Support ticket {form.reference}", reference=form.reference,
        url=form.url or None, opened_at=form.opened_at, incident_start=form.incident_start if complete else None,
        incident_end=form.incident_end if complete else None, simulated=a["simulated"])
    evidence = add_evidence(store, a["vendor_id"], ticket, "incident_action", sample_text) if a["vendor_id"] else None
    value = form.model_dump(mode="json") | {"id": f"action-{alert_id}", "alert_id": alert_id, "incident_key": a["incident_key"],
        "evidence_id": evidence["id"] if evidence else None, "saved_at": utcnow(), "simulated": a["simulated"],
        "ticket_during_incident": form.incident_start <= form.opened_at < form.incident_end if complete else None}
    store.save_record("incident_action", value)
    store.event("ticket", "Support ticket reference recorded", f"{a['customer']} · {form.reference}; supplied metadata, no vendor submission performed.")
    return action_panel(store, watcher, alert_id)
