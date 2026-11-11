"""The extraction batch spends real money: its budget stop and held-out freeze must hold."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from run_extractions import run_batch  # noqa: E402

from shark.extract.citations import source_hash  # noqa: E402


def setup(tmp_path, frozen=False):
    (tmp_path / "gold").mkdir()
    (tmp_path / "a.txt").write_text("Agreement A.")
    (tmp_path / "b.txt").write_text("Agreement B.")
    docs = [{"id": "a", "split": "tuning", "source_path": "../a.txt", "source_hash": source_hash("Agreement A."), "selection": {"service": "S", "plan": "P"}},
            {"id": "b", "split": "held_out", "source_path": "../b.txt", "source_hash": source_hash("Agreement B."), "selection": {"service": "S", "plan": "P"}}]
    manifest = tmp_path / "gold" / "manifest.json"
    manifest.write_text(json.dumps({"frozen_config": {"frozen": frozen, "model": "claude-sonnet-5-5", "prompt_version": "cited-terms-v2"}, "documents": docs}))
    return manifest


def fake_extractor(calls):
    def extract(source, service, plan, cache_dir, run):
        calls.append(run)
        return {"mode": "live", "source_hash": source_hash(source), "model": "claude-sonnet-5-5", "served_by": "claude-sonnet-5-5", "run": run,
                "usage": {"input_tokens": 100_000, "output_tokens": 30_000}, "terms": {}}  # $0.50 per call at Sonnet 5.5 prices
    return extract


def test_batch_stops_before_the_budget_and_resumes(tmp_path):
    manifest, calls = setup(tmp_path), []
    report = run_batch(manifest, tmp_path / "out", split="tuning", runs=3, budget=1.2, extractor=fake_extractor(calls))
    assert calls == [1, 2] and report["stopped_for_budget"] and report["spent_usd"] == pytest.approx(1.0)
    saved = json.loads(manifest.read_text())["documents"][0]["extraction_paths"]
    assert len(saved) == 2 and all((manifest.parent / p).exists() for p in saved)
    calls.clear()
    report = run_batch(manifest, tmp_path / "out", split="tuning", runs=3, budget=1.2, extractor=fake_extractor(calls))
    assert calls == [3] and len(json.loads(manifest.read_text())["documents"][0]["extraction_paths"]) == 3


def test_held_out_runs_require_a_frozen_configuration(tmp_path):
    manifest, calls = setup(tmp_path), []
    with pytest.raises(SystemExit, match="Freeze"):
        run_batch(manifest, tmp_path / "out", split="held_out", runs=3, budget=5, extractor=fake_extractor(calls))
    assert calls == []
