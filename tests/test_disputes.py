"""Dispute window: Atlassian allows 3 business days to ask for a revisit of the awarded credit."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from shark.engine.disputes import revisit_window
from shark.pipeline import Pipeline
from shark.storage import Store

FRIDAY = datetime(2026, 10, 30, 15, 0, tzinfo=timezone.utc)


def test_a_friday_award_can_be_revisited_until_wednesday():
    earliest, utc_reading, latest = revisit_window(FRIDAY, 3)
    assert earliest == datetime(2026, 11, 4, 9, 59, 59, 999999, tzinfo=timezone.utc)   # end of Wednesday at UTC+14
    assert utc_reading == datetime(2026, 11, 4, 23, 59, 59, 999999, tzinfo=timezone.utc)
    assert latest == datetime(2026, 11, 5, 11, 59, 59, 999999, tzinfo=timezone.utc)    # end of Wednesday at UTC-12


def sent_claim(tmp_path):
    p = Pipeline(Store(str(tmp_path / "db.sqlite3")))
    _, key = p.run("jira-sept-claim")
    p.create_claim("jira-sept-claim")
    for action in ("approve", "mark_sent"):
        p.transition("jira-sept-claim", action, key)
    return p, key


def test_a_short_award_opens_a_revisit_window_with_a_drafted_request(tmp_path):
    p, key = sent_claim(tmp_path)
    claim = p.transition("jira-sept-claim", "record_award", key, amount=Decimal(500), now=FRIDAY)
    assert claim["status"] == "short_paid" and claim["award_amount"] == "500.00"
    assert claim["dispute_deadline"].startswith("2026-11-04T09:59:59")
    body = claim["dispute_body"]
    assert "USD 500.00" in body and "USD 900.00" in body and "USD 400.00" in body
    assert "you have 3 business days to request that we revisit the claim" in body
    assert p.transition("jira-sept-claim", "send_dispute", key, now=FRIDAY + timedelta(days=1))["status"] == "disputed"
    final = p.transition("jira-sept-claim", "match_credit", key, amount=Decimal(900))
    assert final["status"] == "credited" and [e["action"] for e in final["events"]][-2:] == ["credited", "simulated_matching_credit"]


def test_a_revisit_after_the_window_is_refused(tmp_path):
    p, key = sent_claim(tmp_path)
    p.transition("jira-sept-claim", "record_award", key, amount=Decimal(500), now=FRIDAY)
    with pytest.raises(ValueError, match="window"):
        p.transition("jira-sept-claim", "send_dispute", key, now=FRIDAY + timedelta(days=7))


def test_accepting_a_short_award_closes_the_claim(tmp_path):
    p, key = sent_claim(tmp_path)
    p.transition("jira-sept-claim", "record_award", key, amount=Decimal(500), now=FRIDAY)
    claim = p.transition("jira-sept-claim", "accept_award", key, now=FRIDAY)
    assert claim["status"] == "credited" and claim["award_amount"] == "500.00"


def test_a_full_award_is_credited_directly(tmp_path):
    p, key = sent_claim(tmp_path)
    assert p.transition("jira-sept-claim", "record_award", key, amount=Decimal(900), now=FRIDAY)["status"] == "credited"


def test_award_needs_an_amount(tmp_path):
    p, key = sent_claim(tmp_path)
    with pytest.raises(ValueError, match="amount"):
        p.transition("jira-sept-claim", "record_award", key)


def test_api_records_a_short_award_and_a_revisit(tmp_path):
    from fastapi.testclient import TestClient
    from api.main import create_app
    with TestClient(create_app(str(tmp_path / "api.sqlite3"))) as client:
        revision = client.get("/api/opportunities/jira-sept-claim").json()["revision"]
        client.post("/api/opportunities/jira-sept-claim/draft", json={"tone": "polite"})
        for action in ("approve", "mark_sent"):
            assert client.post("/api/opportunities/jira-sept-claim/actions", json={"action": action, "revision": revision}).status_code == 200
        short = client.post("/api/opportunities/jira-sept-claim/actions", json={"action": "record_award", "revision": revision, "amount": "500"}).json()
        assert short["status"] == "short_paid" and short["dispute_body"] and short["dispute_deadline"]
        assert client.post("/api/opportunities/jira-sept-claim/actions", json={"action": "send_dispute", "revision": revision}).json()["status"] == "disputed"
        done = client.post("/api/opportunities/jira-sept-claim/actions", json={"action": "match_credit", "revision": revision, "amount": "900"}).json()
        assert done["status"] == "credited" and done["award_amount"] == "900.00"


def test_the_revisit_window_brackets_every_reading_across_a_week():
    from datetime import timedelta
    start = datetime(2026, 11, 2, 0, 0, tzinfo=timezone.utc)
    for hour in range(0, 24 * 7):
        recorded = start + timedelta(hours=hour, minutes=30)
        earliest, utc, latest = revisit_window(recorded, 3)
        assert earliest <= utc <= latest, recorded
    # A Monday 10:30 UTC award: the earliest end is Thursday at UTC+13:15 (still Monday there); the latest is Thursday at UTC-10:30
    # (the first zone where it is Monday) and Friday at UTC+13:30 (already Tuesday), both Fri 10:30 UTC. At UTC-12 it is still Sunday.
    earliest, utc, latest = revisit_window(datetime(2026, 11, 2, 10, 30, tzinfo=timezone.utc), 3)
    assert earliest == datetime(2026, 11, 5, 10, 44, 59, 999999, tzinfo=timezone.utc)
    assert utc == datetime(2026, 11, 5, 23, 59, 59, 999999, tzinfo=timezone.utc)
    assert latest == datetime(2026, 11, 6, 10, 29, 59, 999999, tzinfo=timezone.utc)
