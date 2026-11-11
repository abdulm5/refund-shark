"""Reviewable outbox. Queueing never sends; delivery is an explicit action."""
import hashlib
import json
import os
import smtplib
import ssl
from datetime import datetime, timezone
from email.message import EmailMessage
from urllib.parse import urlparse

import httpx

from shark.storage import utcnow


def configuration() -> dict:
    webhook = urlparse(os.getenv("SHARK_SLACK_WEBHOOK", ""))
    return {"delivery_enabled": os.getenv("SHARK_ENABLE_DELIVERY") == "1",
            "email_configured": bool(os.getenv("SHARK_SMTP_HOST") and os.getenv("SHARK_SMTP_FROM")),
            "slack_configured": webhook.scheme == "https" and webhook.hostname == "hooks.slack.com" and webhook.path.startswith("/services/"),
            "slack_destination": os.getenv("SHARK_SLACK_CHANNEL_LABEL", "Configured Slack channel")}


def sync_outbox(store, pipeline):
    owners = {v["id"]: v for v in store.records("vendor")}
    items = []
    for alert in store.alerts():
        if alert["kind"] == "ticket_now" and not alert["incident_open"]:
            continue
        owner = owners.get(alert["customer_id"], {})
        # Demo directory alerts have no deliverable recipient; they remain downloadable previews.
        items.append((f"alert-{alert['id']}", alert["customer_id"], owner, alert["title"],
                      f"{alert['customer']}\n\n{alert['detail']}\n\n{alert.get('ticket') or ''}", alert["simulated"]))
    for reminder in pipeline.reminders():
        s = pipeline.scenarios[reminder["id"]]
        if not s.simulated and datetime.fromisoformat(reminder["deadline"]) < datetime.now(timezone.utc):
            continue
        owner = owners.get(s.account.customer_id, {})
        if not owner:
            continue
        items.append((f"deadline-{s.id}-{reminder['deadline']}", s.account.customer_id, owner,
            f"{s.product}: {'file the claim' if reminder['kind'] == 'file' else 'collect missing evidence'}",
            f"{owner['customer']}\nClaim month: {s.period}\nFile by {reminder['deadline']}\n"
            f"Missing: {', '.join(reminder['missing']) or 'Nothing listed; review and approve the claim.'}\n"
            f"Evaluated at {s.as_of.isoformat()}. Check today's deadline before filing.", s.simulated))
    active = {item[0] for item in items}
    for old in store.records("notification"):
        owner = owners.get(old["vendor_id"], {})
        if old["status"] == "draft" and (old["source_id"] not in active or old["recipient"] != owner.get("owner_email", "")):
            old.update(status="superseded", error="The incident, claim, or owner changed. Use the current follow-up draft.")
            store.save_record("notification", old)
    for source_id, vendor_id, owner, subject, body, simulated in items:
        recipient = owner.get("owner_email", "")
        title = ("[SIMULATED] " if simulated else "") + subject
        content = ("SIMULATED DEMO — do not file with a vendor.\n\n" if simulated else "") + body
        # Routing changes create a new draft rather than silently changing an already delivered message.
        id = "notice-" + hashlib.sha256(f"{source_id}|{recipient}".encode()).hexdigest()[:24]
        store.save_record("notification", {"id": id, "source_id": source_id, "vendor_id": vendor_id,
            "recipient": recipient, "owner": owner.get("owner_name", "Unassigned"), "subject": title, "body": content,
            "simulated": simulated, "status": "draft", "created_at": utcnow(), "channel": None, "delivered_at": None, "error": None}, only_new=True)
    return store.records("notification")


def email_message(item: dict) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = " ".join(item["subject"].splitlines())
    if item["recipient"]:
        message["To"] = item["recipient"]
    message["From"] = os.getenv("SHARK_SMTP_FROM", "Refund Shark <draft@localhost>")
    message["X-Unsent"] = "1"
    message["Message-ID"] = f"<{item['id']}@refund-shark.local>"
    message.set_content(item["body"])
    return message


def send_email(item):
    message = email_message(item)
    del message["X-Unsent"]
    host = os.environ["SHARK_SMTP_HOST"]
    port = int(os.getenv("SHARK_SMTP_PORT", "587"))
    with smtplib.SMTP(host, port, timeout=15) as smtp:
        smtp.ehlo()
        smtp.starttls(context=ssl.create_default_context())
        smtp.ehlo()
        if os.getenv("SHARK_SMTP_USERNAME"):
            smtp.login(os.environ["SHARK_SMTP_USERNAME"], os.environ.get("SHARK_SMTP_PASSWORD", ""))
        if smtp.send_message(message):
            raise ValueError("The mail server refused the recipient.")


def send_slack(item):
    # Credentials are env-only; don't follow redirects or expose response/error bodies.
    content = f"{item['subject']}\nOwner: {item['owner']} ({item['recipient']})\n\n{item['body']}"
    response = httpx.post(os.environ["SHARK_SLACK_WEBHOOK"], json={"text": content, "mrkdwn": False,
        "blocks": [{"type": "section", "text": {"type": "plain_text", "text": content[:2900]}}]}, timeout=15, follow_redirects=False)
    if response.status_code != 200 or response.text.strip() != "ok":
        raise ValueError("Slack did not confirm delivery.")


def deliver(store, id: str, channel: str, pipeline=None) -> dict:
    if pipeline is not None:
        sync_outbox(store, pipeline)
    config = configuration()
    if not config["delivery_enabled"]:
        raise ValueError("Delivery is off. Set SHARK_ENABLE_DELIVERY=1 on the server after configuring a channel.")
    if not config[f"{channel}_configured"]:
        raise ValueError(f"Configure {channel} on the server first.")
    # Claim the draft atomically before touching a network. Failures are not auto-retried: a timeout may have sent it.
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT payload FROM workspace_records WHERE kind='notification' AND id=?", (id,)).fetchone()
        if not row:
            raise ValueError("That notification does not exist.")
        item = json.loads(row[0])
        if item["status"] != "draft":
            raise ValueError("This message was already attempted. Check its delivery history to avoid a duplicate.")
        if not item["recipient"]:
            raise ValueError("Assign this vendor an owner email before sending.")
        current = store.record("vendor", item["vendor_id"])
        if not current or current["owner_email"] != item["recipient"]:
            raise ValueError("The owner changed. Refresh the outbox and use the new draft.")
        item.update(status="sending", channel=channel, attempted_at=utcnow())
        db.execute("UPDATE workspace_records SET payload=? WHERE kind='notification' AND id=?", (json.dumps(item), id))
    try:
        (send_email if channel == "email" else send_slack)(item)
        item.update(status="sent", delivered_at=utcnow())
    except Exception:
        item.update(status="uncertain", error="Delivery was not confirmed. Check the destination before trying to send another message.")
    store.save_record("notification", item)
    store.event("notification", f"Alert delivery {item['status']}", f"{channel} · {item['subject']}")
    return item
