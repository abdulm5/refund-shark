"""Exercise the recovery workflow across PDF input, storage, case pricing, alerts, and later invoices."""
import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from reportlab.pdfgen.canvas import Canvas
from reportlab.lib.pdfencrypt import StandardEncryption

import api.main
from api.main import create_app
from shark.documents import citation_pages, parse_pdf, suggestions
from shark.storage import Store
from conftest import scenario

FIXTURES = Path(__file__).resolve().parents[1] / "data/fixtures/documents"


@pytest.fixture
def client(tmp_path, monkeypatch):
    for k in ("SHARK_ENABLE_DELIVERY", "SHARK_SLACK_WEBHOOK", "SHARK_SMTP_HOST", "SHARK_SMTP_FROM", "SHARK_EVIDENCE_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    with TestClient(create_app(str(tmp_path / "db.sqlite3"), status_get=lambda *a, **k: {"incidents": []})) as c:
        yield c


def upload(client, name="invoice"):
    r = client.post(f"/api/documents?kind={'contract' if name == 'contract' else 'invoice'}&filename=sample-{name}.pdf", content=(FIXTURES / f"sample-{name}.pdf").read_bytes())
    assert r.status_code == 200, r.text
    return r.json()


def confirm(client, name="invoice", **updates):
    d = upload(client, name)
    fields = {k: v["value"] for k, v in d["suggestions"].items()} | {"reviewed": True} | updates
    r = client.post(f"/api/documents/{d['id']}/confirm", json=fields)
    assert r.status_code == 200, r.text
    return r.json()


def setup(client, **updates):
    doc = confirm(client)
    read = client.post("/api/agreements/read", json={"catalog_id": "atlassian"}).json()
    inv = doc["confirmed"]
    body = {k: inv[k] for k in ("customer", "vendor", "product", "plan", "account_reference", "billing_cycle")}
    body.update(region="US East", owner_name="Finance", owner_email="finance@example.com", read_id=read["read_id"],
                invoice_id=doc["id"], confirmed=True, **updates)
    r = client.post("/api/vendors", json=body)
    assert r.status_code == 200, r.text
    return r.json(), read


def evidence(client, vendor, kind="logs", **updates):
    if kind == "logs":
        body = {"kind": "logs", "period": "2026-09", "label": "Request export", "csv": client.get("/api/logs/sample.csv").text}
    else:
        body = {"kind": "ticket", "period": "2026-09", "label": "Support reference", "reference": "SUP-1", "opened_at": "2026-09-03T16:00:00Z",
                "incident_start": "2026-09-03T15:00:00Z", "incident_end": "2026-09-03T17:30:00Z"}
    r = client.post(f"/api/vendors/{vendor['id']}/evidence", json=body | updates)
    assert r.status_code == 200, r.text
    return r.json()


def sent_claim(client, id="jira-sept-claim"):
    r = client.post(f"/api/opportunities/{id}/draft", json={"tone": "polite"})
    assert r.status_code == 200, r.text
    revision = client.get(f"/api/opportunities/{id}").json()["revision"]
    for action in ("approve", "mark_sent"):
        r = client.post(f"/api/opportunities/{id}/actions", json={"action": action, "revision": revision})
        assert r.status_code == 200, r.text
    return client.get(f"/api/opportunities/{id}").json()["claim"]


def test_pdf_upload_preserves_pages_original_hash_and_duplicate_review(client):
    d = confirm(client)
    assert d["simulated"] and d["page_count"] == 1
    assert d["suggestions"]["fees"] == {"value": "9000.00", "page": 1, "quote": "Product fee: 9000.00"}
    assert client.get(f"/api/documents/{d['id']}/file").content == (FIXTURES / "sample-invoice.pdf").read_bytes()
    assert upload(client)["confirmed"] == d["confirmed"]
    assert client.post(f"/api/documents/{d['id']}/confirm", json=d["confirmed"]).status_code == 409


@pytest.mark.parametrize("data", [b"not a PDF", b"%PDF-1.4\ncorrupt"])
def test_invalid_pdf_refuses(client, data):
    assert client.post("/api/documents?kind=contract", content=data).status_code == 422


def test_blank_encrypted_and_oversized_pdf_refuse(client):
    for encrypted in (False, True):
        b = io.BytesIO()
        c = Canvas(b, encrypt=StandardEncryption("secret") if encrypted else None)
        if encrypted:
            c.drawString(20, 20, "Protected contract")
        c.showPage(); c.save()
        assert client.post("/api/documents?kind=contract", content=b.getvalue()).status_code == 422
    assert client.post("/api/documents?kind=contract", content=b"%PDF-" + b"x" * 10_000_000).status_code == 413


def test_ambiguous_invoice_labels_and_cross_page_citations():
    assert "fees" not in suggestions([{"number": 1, "text": "Product fee: 100.00\nProduct fee: 500.00"}])
    assert citation_pages({"tier": {"quote": "ten percent of fees […] next invoice"}},
        [{"number": 1, "text": "ten percent"}, {"number": 2, "text": "of fees and on the next invoice"}]) == {"tier": [1, 2]}


def test_pdf_read_uses_existing_extractor_and_returns_page_citations(client, monkeypatch):
    d = upload(client, "contract")
    terms = scenario("jira-sept-claim").terms.model_dump(mode="json")
    seen = []
    def fake(text, service, plan, cache):
        seen.append(text)
        return {"terms": terms, "mode": "live", "model": "test"}
    monkeypatch.setattr(api.main, "extract", fake)
    r = client.post("/api/agreements/read", json={"document_id": d["id"], "service": "Jira", "plan": "Premium"})
    assert r.status_code == 200, r.text
    assert seen and r.json()["source"]["document_id"] == d["id"]
    assert r.json()["page_citations"]["credit_tiers"]
    stored = client.app.state.pipeline.store.get_read(r.json()["read_id"])
    assert stored["source_kind"] == "pdf"


def test_invoice_credit_requires_review_and_reference(client):
    d = upload(client, "credit")
    fields = {k: v["value"] for k, v in d["suggestions"].items()}
    assert client.post(f"/api/documents/{d['id']}/confirm", json=fields).status_code == 422
    assert client.post(f"/api/documents/{d['id']}/confirm", json=fields | {"reviewed": True, "claim_reference": None}).status_code == 422


def test_vendor_setup_matching_and_restart(client):
    vendor, _ = setup(client)
    assert all(c["done"] for c in vendor["checklist"])
    client.post("/api/watch/drill", json={"action": "start"})
    alerts = client.get("/api/alerts").json()["alerts"]
    assert any(a["customer_id"] == vendor["id"] and a["kind"] == "ticket_now" for a in alerts)
    db = client.app.state.pipeline.store.path
    with TestClient(create_app(db, status_get=lambda *a, **k: {"incidents": []})) as restarted:
        assert restarted.get("/api/vendors").json()["vendors"][0]["id"] == vendor["id"]
        matches = restarted.get("/api/alerts").json()["incidents"][0]["matches"]
        assert any(m["customer_id"] == vendor["id"] for m in matches)


def test_wrong_plan_stays_incomplete_and_cannot_create_case(client):
    vendor, _ = setup(client, plan="Enterprise")
    assert not all(c["done"] for c in vendor["checklist"])
    assert client.post(f"/api/vendors/{vendor['id']}/cases", json={"evidence_id": "x", "as_of": "2026-10-08T12:00:00Z"}).status_code == 422


def test_evidence_integration_authentication_idempotency_and_measurement(client, monkeypatch):
    v, _ = setup(client)
    body = {"kind": "logs", "label": "Monitoring export", "period": "2026-09", "csv": client.get("/api/logs/sample.csv").text}
    path = f"/api/integrations/evidence/{v['id']}"
    assert client.post(path, json=body).status_code == 401
    monkeypatch.setenv("SHARK_EVIDENCE_TOKEN", "test-only-token")
    assert client.post(path, json=body, headers={"Authorization": "Bearer wrong"}).status_code == 401
    r = client.post(path, json=body, headers={"Authorization": "Bearer test-only-token"})
    assert r.status_code == 200, r.text
    assert r.json()["analysis"]["downtime_minutes"] == 62 and r.json()["source"] == "integration"
    again = client.post(path, json=body, headers={"Authorization": "Bearer test-only-token"})
    assert again.json()["id"] == r.json()["id"]
    assert len(client.get(f"/api/vendors/{v['id']}/evidence").json()["evidence"]) == 1


@pytest.mark.parametrize("opened, expected", [("2026-09-03T14:59:00Z", False), ("2026-09-03T15:00:00Z", True), ("2026-09-03T17:30:00Z", False)])
def test_ticket_window_boundaries(client, opened, expected):
    v, _ = setup(client)
    assert evidence(client, v, "ticket", opened_at=opened)["ticket_during_incident"] is expected


def test_vendor_evidence_creates_real_engine_case_without_mutating_other_claims(client):
    v, read = setup(client)
    log = evidence(client, v)
    ticket = evidence(client, v, "ticket")
    later = confirm(client, "credit")
    form = {"evidence_id": log["id"], "ticket_id": ticket["id"], "receiving_invoice_id": later["id"], "as_of": "2026-10-08T12:00:00Z",
            "prerequisites": {p: True for p in read["case_prerequisites"]}, "exclusions_reviewed": True}
    r = client.post(f"/api/vendors/{v['id']}/cases", json=form)
    assert r.status_code == 200, r.text
    assert r.json()["decision"]["amount"] == "900.00" and r.json()["decision"]["outcome"] == "eligible"
    assert r.json()["scenario"]["invoice"]["id"] == "DEMO-SEP-001"
    assert r.json()["scenario"]["simulated"]
    assert client.get("/api/opportunities/jira-sept-claim").json()["claim"] is None
    # A checkbox cannot substitute for a missing ticket or override known failure.
    form["ticket_id"] = None
    form.update(confirm_update=True, expected_revision=r.json()["revision"])
    missing = client.post(f"/api/vendors/{v['id']}/cases", json=form).json()
    assert missing["decision"]["outcome"] == "needs_review"
    late = evidence(client, v, "ticket", opened_at="2026-09-04T12:00:00Z")
    form["ticket_id"] = late["id"]
    form["expected_revision"] = missing["revision"]
    assert client.post(f"/api/vendors/{v['id']}/cases", json=form).json()["decision"]["outcome"] == "not_eligible"


def test_outbox_preview_dedup_disabled_delivery_and_mocked_send(client, monkeypatch):
    v, _ = setup(client)
    client.post("/api/watch/drill", json={"action": "start"})
    items = client.get("/api/notifications").json()["notifications"]
    n = next(n for n in items if n["vendor_id"] == v["id"])
    assert client.get("/api/notifications").json()["notifications"] == items
    assert b"X-Unsent: 1" in client.get(f"/api/notifications/{n['id']}/email").content
    body = {"channel": "email", "confirmed": True}
    assert client.post(f"/api/notifications/{n['id']}/send", json=body).status_code == 422
    monkeypatch.setenv("SHARK_ENABLE_DELIVERY", "1")
    monkeypatch.setenv("SHARK_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SHARK_SMTP_FROM", "alerts@example.com")
    sent = []
    monkeypatch.setattr("shark.notifications.send_email", lambda item: sent.append(item["id"]))
    assert client.post(f"/api/notifications/{n['id']}/send", json=body).json()["status"] == "sent"
    assert client.post(f"/api/notifications/{n['id']}/send", json=body).status_code == 422
    assert sent == [n["id"]]


def test_delivery_timeout_does_not_silently_retry_or_leak_secret(client, monkeypatch):
    v, _ = setup(client)
    client.post("/api/watch/drill", json={"action": "start"})
    n = next(n for n in client.get("/api/notifications").json()["notifications"] if n["vendor_id"] == v["id"])
    monkeypatch.setenv("SHARK_ENABLE_DELIVERY", "1")
    monkeypatch.setenv("SHARK_SLACK_WEBHOOK", "https://hooks.slack.com/services/test/secret/token")
    def fail(_):
        raise TimeoutError("do not echo this secret")
    monkeypatch.setattr("shark.notifications.send_slack", fail)
    r = client.post(f"/api/notifications/{n['id']}/send", json={"channel": "slack", "confirmed": True})
    assert r.json()["status"] == "uncertain" and "secret" not in r.text


@pytest.mark.parametrize("amount, outcome", [("900", "matched"), ("450", "partial"), ("0", "missing"), ("1200", "excess")])
def test_invoice_reconciliation_amounts_and_claim_state(client, amount, outcome):
    claim = sent_claim(client)
    d = confirm(client, "credit", credit_amount=amount)
    r = client.post("/api/reconciliations", json={"case_id": "jira-sept-claim", "document_id": d["id"]})
    assert r.status_code == 200, r.text
    assert r.json()["outcome"] == outcome and r.json()["submission_simulated"]
    assert client.get("/api/opportunities/jira-sept-claim").json()["claim"] == claim


@pytest.mark.parametrize("field,value", [("customer", "Other business"), ("account_reference", "OTHER"), ("claim_reference", "WRONG"), ("currency", "EUR"), ("period", "2026-09"), ("credit_period", "2026-08")])
def test_invoice_identity_mismatch_never_matches(client, field, value):
    sent_claim(client)
    d = confirm(client, "credit", **{field: value})
    r = client.post("/api/reconciliations", json={"case_id": "jira-sept-claim", "document_id": d["id"]})
    assert r.json()["outcome"] == "mismatch"
    assert r.json()["difference"] is None
    assert r.json()["currency"] == "USD"
    assert r.json()["observed_currency"] == (value if field == "currency" else "USD")
    assert any(not check["done"] for check in r.json()["checks"])


def test_reconciliation_requires_sent_claim(client):
    d = confirm(client, "credit")
    assert client.post("/api/reconciliations", json={"case_id": "jira-sept-claim", "document_id": d["id"]}).status_code == 422


def test_resolved_incident_retires_urgent_draft(client):
    v, _ = setup(client)
    client.post("/api/watch/drill", json={"action": "start"})
    n = next(n for n in client.get("/api/notifications").json()["notifications"] if n["vendor_id"] == v["id"])
    client.post("/api/watch/drill", json={"action": "end"})
    items = client.get("/api/notifications").json()["notifications"]
    assert next(i for i in items if i["id"] == n["id"])["status"] == "superseded"
    assert any(i["vendor_id"] == v["id"] and i["status"] == "draft" and "resolved" in i["subject"] for i in items)


def test_reassigned_owner_cannot_receive_old_draft(client):
    v, _ = setup(client)
    client.post("/api/watch/drill", json={"action": "start"})
    old = next(n for n in client.get("/api/notifications").json()["notifications"] if n["vendor_id"] == v["id"])
    form = {k: val for k, val in v.items() if k not in {"id", "checklist", "updated_at"}}
    form["owner_email"] = "new-owner@example.com"
    assert client.put(f"/api/vendors/{v['id']}", json=form).status_code == 200
    items = client.get("/api/notifications").json()["notifications"]
    assert next(i for i in items if i["id"] == old["id"])["status"] == "superseded"
    assert any(i["recipient"] == "new-owner@example.com" and i["status"] == "draft" for i in items)


def test_smtp_adapter_requires_tls_and_preserves_recipient(monkeypatch):
    from shark.notifications import send_email
    calls = []
    class SMTP:
        def __init__(self, host, port, timeout): calls.append((host, port))
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def ehlo(self): calls.append("ehlo")
        def starttls(self, context): calls.append("tls")
        def login(self, user, password): calls.append("login")
        def send_message(self, message):
            assert "X-Unsent" not in message
            assert message["To"] == "finance@example.com"
            calls.append("sent")
            return {}
    monkeypatch.setattr("shark.notifications.smtplib.SMTP", SMTP)
    monkeypatch.setenv("SHARK_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SHARK_SMTP_FROM", "alerts@example.com")
    monkeypatch.setenv("SHARK_SMTP_USERNAME", "sender")
    monkeypatch.setenv("SHARK_SMTP_PASSWORD", "never-printed")
    send_email({"id": "test", "subject": "Action required", "recipient": "finance@example.com", "body": "Open a ticket."})
    assert calls.index("tls") < calls.index("login") < calls.index("sent")


def test_slack_adapter_posts_plain_text_without_following_redirects(monkeypatch):
    from shark.notifications import send_slack
    monkeypatch.setenv("SHARK_SLACK_WEBHOOK", "https://hooks.slack.com/services/test/test/test")
    def post(url, json, timeout, follow_redirects):
        assert url.startswith("https://hooks.slack.com/services/") and follow_redirects is False
        assert json["blocks"][0]["text"]["type"] == "plain_text" and json["mrkdwn"] is False
        return type("Response", (), {"status_code": 200, "text": "ok"})()
    monkeypatch.setattr("shark.notifications.httpx.post", post)
    send_slack({"subject": "Test", "owner": "Finance", "recipient": "finance@example.com", "body": "Open a ticket."})
