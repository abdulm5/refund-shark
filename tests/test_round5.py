"""Fixes for the remaining review minors."""
import json
from decimal import Decimal

import pytest

from conftest import scenario
from shark.engine import evaluate
from shark.engine.metrics import apply_logs
from shark.models import Scenario
from shark.pipeline import ROOT, Pipeline
from shark.storage import Store


def test_a_credit_that_rounds_to_zero_is_not_eligible():
    s = scenario("jira-sept-claim")
    s.invoice.affected_fees = Decimal("0.04")  # 10% is $0.004, which rounds to $0.00
    d = evaluate(s)
    assert d.outcome == "not_eligible" and d.amount == 0 and "$0.00" in d.reason


def test_verify_sources_fails_loudly_on_a_changed_excerpt(monkeypatch, capsys):
    import shark.cli as cli
    real = cli.load_manifest()
    broken = {**real, "sources": [{**real["sources"][0], "excerpt_sha256": "0" * 64}] + real["sources"][1:]}
    monkeypatch.setattr(cli, "load_manifest", lambda: broken)
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["verify-sources"])
    assert exit_info.value.code == 1 and '"excerpt_intact": false' in capsys.readouterr().out


def test_redrafting_keeps_the_claim_history(tmp_path):
    p = Pipeline(Store(str(tmp_path / "db.sqlite3")))
    p.create_claim("jira-sept-claim", "polite")
    claim = p.create_claim("jira-sept-claim", "firm")
    assert [e["action"] for e in claim["events"]] == ["drafted", "redrafted"] and claim["tone"] == "firm"


def test_logs_under_a_metric_they_cannot_measure_go_to_review():
    raw = next(s for s in json.loads((ROOT / "data/fixtures/scenarios.json").read_text()) if s["id"] == "jira-sept-claim")
    s = Scenario.model_validate(raw)
    s.terms.downtime_definition.value.kind = "availability_minutes"
    s.terms.downtime_definition.value.error_threshold_pct = None
    d = evaluate(apply_logs(s))
    record = next(i for i in s.incidents if i.log)
    assert record.metric_verified is False and "cannot be measured" in record.notes
    assert d.outcome == "needs_review" and any("not been verified" in c.detail for c in d.checks if c.status == "review")
