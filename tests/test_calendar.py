"""Calendar export: a claim deadline and its reminder, as a standards-compliant .ics file."""
from fastapi.testclient import TestClient

from api.main import create_app
from shark.calendar import ics_event


def test_ics_event_is_rfc5545_shaped():
    from datetime import datetime, timezone
    text = ics_event("uid-1@refund-shark", datetime(2026, 10, 15, 9, 59, 59, tzinfo=timezone.utc), "File: " + "x" * 120,
                     "Line one\nLine, two; three", "https://example.com/case")
    assert text.startswith("BEGIN:VCALENDAR\r\n") and text.endswith("END:VCALENDAR\r\n")
    assert "DTSTART:20261015T095959Z" in text and "TRIGGER:-P1D" in text
    assert "Line one\\nLine\\, two\\; three" in text.replace("\r\n ", "")
    assert "DTEND" not in text  # a deadline is an instant; RFC 5545 requires DTEND to be later than DTSTART
    assert all(len(line.encode()) <= 75 for line in text.split("\r\n"))


def test_claim_deadline_downloads_as_a_calendar_event(tmp_path):
    with TestClient(create_app(str(tmp_path / "db.sqlite3"))) as client:
        response = client.get("/api/opportunities/jira-sept-claim/deadline.ics")
        assert response.status_code == 200 and response.headers["content-type"].startswith("text/calendar")
        body = response.text.replace("\r\n ", "")
        assert "DTSTART:20261015T095959Z" in body and "Northstar Analytics" in body and "earliest reading" in body
        assert client.get("/api/opportunities/github/deadline.ics").status_code == 409  # no computed deadline
