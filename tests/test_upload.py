"""Measure your own logs: a what-if priced by the engine, never stored."""
from fastapi.testclient import TestClient

from api.main import create_app


def client(tmp_path):
    return TestClient(create_app(str(tmp_path / "db.sqlite3")))


def test_sample_log_turns_missing_evidence_into_a_priced_what_if(tmp_path):
    with client(tmp_path) as c:
        sample = c.get("/api/logs/sample.csv")
        assert sample.status_code == 200 and sample.text.startswith("minute,experience,requests,errors")
        body = c.post("/api/opportunities/jira-sept-missing-evidence/logs/analyze", json={"csv": sample.text}).json()
        assert body["what_if"] is True and body["analysis"]["downtime_minutes"] == 62 and body["analysis"]["minutes_covered"] == 43200
        d = body["decision"]
        counted = [e for e in d["estimates"] if e["counted"]]
        assert counted and counted[0]["amount"] == "480.00" and d["outcome"] == "needs_review"
        blockers = {ch["name"] for ch in d["checks"] if ch["status"] == "review"}
        assert "Customer evidence" not in blockers and {"Support ticket during incident", "Credit cap"} <= blockers
        stored = c.get("/api/opportunities/jira-sept-missing-evidence").json()["decision"]
        assert "postmortem" in stored["estimate_basis"]  # the stored case is unchanged


def test_upload_never_touches_an_approved_claim(tmp_path):
    with client(tmp_path) as c:
        detail = c.get("/api/opportunities/jira-sept-claim").json()
        c.post("/api/opportunities/jira-sept-claim/draft", json={"tone": "polite"})
        c.post("/api/opportunities/jira-sept-claim/actions", json={"action": "approve", "revision": detail["revision"]})
        what_if = c.post("/api/opportunities/jira-sept-claim/logs/analyze", json={"csv": c.get("/api/logs/sample.csv").text}).json()
        assert what_if["decision"]["outcome"] == "eligible" and what_if["decision"]["downtime_minutes"] == "62"
        assert c.get("/api/opportunities/jira-sept-claim").json()["claim"]["status"] == "approved"


def test_bad_uploads_are_explained(tmp_path):
    with client(tmp_path) as c:
        wrong_columns = c.post("/api/opportunities/jira-sept-claim/logs/analyze", json={"csv": "time,status\n2026-09-01T00:00Z,500\n"})
        assert wrong_columns.status_code == 422 and "minute,experience,requests,errors" in wrong_columns.json()["detail"]
        other_month = c.post("/api/opportunities/jira-sept-claim/logs/analyze",
                             json={"csv": "minute,experience,requests,errors\n2026-08-01T00:00Z,View Issue,10,0\n"})
        assert other_month.status_code == 422 and "2026-09" in other_month.json()["detail"]
        unmeasurable = c.post("/api/opportunities/cloudflare/logs/analyze", json={"csv": c.get("/api/logs/sample.csv").text})
        assert unmeasurable.status_code == 422 and "request error rate" in unmeasurable.json()["detail"]
        assert c.post("/api/opportunities/nope/logs/analyze", json={"csv": "x"}).status_code == 404
