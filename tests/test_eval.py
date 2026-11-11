import json
from decimal import Decimal

from conftest import scenario
from eval.run_eval import GOLD_MANIFEST, score_suite
from shark.extract.citations import source_hash


def test_manifest_declares_six_tuning_and_two_held_out_documents():
    manifest = json.loads(GOLD_MANIFEST.read_text())
    splits = [d["split"] for d in manifest["documents"]]
    assert splits.count("tuning") == 6 and splits.count("held_out") == 2
    assert len({d["url"] for d in manifest["documents"]}) == 8
    # The protocol has run: frozen before the held-out batch, and every held-out run made after freezing.
    frozen = manifest["frozen_config"]
    assert frozen["frozen"] is True and frozen["model"] == "claude-sonnet-5-5"
    for doc in (d for d in manifest["documents"] if d["split"] == "held_out"):
        runs = [json.loads((GOLD_MANIFEST.parent / path).read_text()) for path in doc["extraction_paths"]]
        assert len(runs) == 3 and all(r["extracted_at"] > frozen["frozen_at"] and r["prompt_version"] == frozen["prompt_version"]
                                      and r["schema_hash"] == frozen["schema_hash"] for r in runs)


def test_unreviewed_documents_are_reported_not_run():
    manifest = json.loads(GOLD_MANIFEST.read_text())
    results = score_suite(manifest, GOLD_MANIFEST.parent)
    assert {r["status"] for r in results} == {"not_run"}
    assert all(r["reason"] for r in results)


def test_scoring_separates_values_quotes_and_decisions(tmp_path):
    case = scenario("jira-sept-claim")
    (tmp_path / "source.txt").write_text(case.source_text)
    (tmp_path / "gold.json").write_text(case.terms.model_dump_json())
    (tmp_path / "case.json").write_text(case.model_dump_json())
    config = {"model": "test-model", "prompt_version": "test", "schema_hash": "abc"}
    wrong = case.terms.model_copy(deep=True)
    wrong.credit_tiers.value[0].credit_pct = Decimal(50)  # the quote still verifies; the value is wrong
    for index, terms in enumerate([case.terms, case.terms, wrong]):
        (tmp_path / f"run{index}.json").write_text(json.dumps({"mode": "live", "source_hash": case.source_hash, **config, "terms": terms.model_dump(mode="json")}))
    manifest = {"frozen_config": {**config, "frozen": True}, "documents": [{
        "id": "atlassian", "split": "tuning", "url": "https://example.com", "review_status": "human_reviewed",
        "source_path": "source.txt", "source_hash": source_hash(case.source_text), "gold_path": "gold.json",
        "extraction_paths": ["run0.json", "run1.json", "run2.json"], "scenario_paths": ["case.json"]}]}
    [result] = score_suite(manifest, tmp_path)
    assert result["status"] == "scored"
    assert result["worst_field_accuracy"] == 15 / 16
    assert result["worst_quote_match_rate"] == 1  # stated terms only; the confirmed-absent minimum has no quote
    assert result["credit_table_exact_runs"] == 2
    assert result["priced_decision_matches"] == 2 and result["both_review_matches"] == 0
    assert result["false_positive_eligible"] == 0


def saved_runs(tmp_path, case, runs):
    config = {"model": "test-model", "served_by": "test-model", "prompt_version": "test", "schema_hash": "abc", "usage": {"input_tokens": 1000, "output_tokens": 200}}
    paths = []
    for index, terms in enumerate(runs):
        (tmp_path / f"run{index}.json").write_text(json.dumps({"mode": "live", "source_hash": source_hash(case.source_text), **config, "terms": terms.model_dump(mode="json")}))
        paths.append(f"run{index}.json")
    return paths


def test_model_runs_report_label_free_metrics_and_draft_agreement(tmp_path):
    from eval.run_eval import summarize_runs
    case = scenario("jira-sept-claim")
    (tmp_path / "source.txt").write_text(case.source_text)
    (tmp_path / "draft.json").write_text(case.terms.model_dump_json())
    wrong = case.terms.model_copy(deep=True)
    wrong.credit_tiers.value[0].credit_pct = Decimal(50)
    manifest = {"frozen_config": {"frozen": False}, "documents": [
        {"id": "atlassian", "split": "tuning", "review_status": "machine_drafted", "source_path": "source.txt", "source_hash": source_hash(case.source_text),
         "gold_path": "draft.json", "scenario_ids": ["jira-sept-claim"], "extraction_paths": saved_runs(tmp_path, case, [case.terms, case.terms, wrong])},
        {"id": "other", "split": "held_out", "review_status": "not_labeled", "extraction_paths": []}]}
    first, second = summarize_runs(manifest, tmp_path)
    assert first["status"] == "runs_saved" and first["runs"] == 3
    assert first["quote_match_rate_worst"] == 1 and first["run_consistency"] == 15 / 16 and first["inconsistent_fields"] == ["credit_tiers"]
    agreement = first["draft_label_agreement"]
    assert agreement["review_status"] == "machine_drafted" and agreement["worst_field_agreement"] == 15 / 16
    assert agreement["decision_matches"] == 2 and agreement["decision_comparisons"] == 3 and agreement["false_positive_eligible"] == 0
    assert first["tokens"] == {"input": 3000, "output": 600}
    assert second["status"] == "no_runs"


def test_supplemental_page_is_read_with_its_agreement(tmp_path):
    from eval.run_eval import load_source
    (tmp_path / "sla.txt").write_text("Main agreement text.")
    (tmp_path / "help.txt").write_text("Supplemental procedure text.")
    entry = {"id": "atlassian", "title": "SLA", "url": "https://example.com/sla", "source_path": "sla.txt", "source_hash": source_hash("Main agreement text."),
             "supplement": {"title": "Procedure", "url": "https://example.com/help", "source_path": "help.txt", "source_hash": source_hash("Supplemental procedure text.")}}
    text = load_source(entry, tmp_path)
    assert "Main agreement text." in text and "Supplemental procedure text." in text and "=== Procedure" in text
    (tmp_path / "help.txt").write_text("Changed.")
    import pytest
    with pytest.raises(ValueError, match="Changed source"):
        load_source(entry, tmp_path)


def test_saved_runs_are_summarized_without_the_private_source(tmp_path):
    from eval.run_eval import summarize_runs
    case = scenario("jira-sept-claim")
    (tmp_path / "draft.json").write_text(case.terms.model_dump_json())
    status = {name: "verified" for name in type(case.terms).model_fields} | {"min_credit": "absent", "credit_cap": "unverified"}
    for index in range(3):
        (tmp_path / f"run{index}.json").write_text(json.dumps({"mode": "live", "source_hash": "abc", "model": "m", "served_by": "m",
                                                               "terms": case.terms.model_dump(mode="json"), "term_status": status, "usage": {}}))
    manifest = {"frozen_config": {"frozen": False}, "documents": [{
        "id": "atlassian", "split": "tuning", "review_status": "machine_drafted", "raw_path": "missing/page.html", "raw_sha256": "x", "source_hash": "abc",
        "gold_path": "draft.json", "scenario_ids": ["jira-sept-claim"], "extraction_paths": ["run0.json", "run1.json", "run2.json"]}]}
    [doc] = summarize_runs(manifest, tmp_path)
    assert doc["status"] == "runs_saved_source_not_cached" and doc["quote_match_rate_worst"] == 14 / 15
    assert doc["run_consistency"] == 1 and doc["draft_label_agreement"]["worst_field_agreement"] == 1
    assert doc["draft_label_agreement"]["decision_comparisons"] == 0 and "not cached" in doc["draft_label_agreement"]["decisions_skipped"]


def test_material_comparison_ignores_wording_but_not_values():
    from eval.run_eval import compare_fields
    gold = scenario("jira-sept-claim").terms
    reworded = gold.model_copy(deep=True)
    reworded.downtime_definition.value.description = "Said differently."
    reworded.exclusions.value[0].description = "Also said differently."
    assert compare_fields(gold, reworded)["field_accuracy"] == 1
    zoned = gold.model_copy(deep=True)
    zoned.claim_window.value.timezone = "UTC"
    assert compare_fields(gold, zoned)["fields"]["claim_window"] is False


def test_consistency_compares_material_values(tmp_path):
    from eval.run_eval import summarize_runs
    case = scenario("jira-sept-claim")
    (tmp_path / "source.txt").write_text(case.source_text)
    reworded = case.terms.model_copy(deep=True)
    reworded.downtime_definition.value.description = "Same rule, other words."
    manifest = {"frozen_config": {"frozen": False}, "documents": [
        {"id": "a", "split": "tuning", "source_path": "source.txt", "source_hash": source_hash(case.source_text),
         "extraction_paths": saved_runs(tmp_path, case, [case.terms, reworded, case.terms])}]}
    [doc] = summarize_runs(manifest, tmp_path)
    assert doc["run_consistency"] == 1 and doc["inconsistent_fields"] == []


def test_frozen_configuration_still_matches_the_code():
    # The README's evaluation describes runs of this exact prompt and schema; changing either needs a new frozen batch.
    from shark.extract.provider import PROMPT_VERSION, schema_hash
    frozen = json.loads(GOLD_MANIFEST.read_text())["frozen_config"]
    assert (schema_hash(), PROMPT_VERSION) == (frozen["schema_hash"], frozen["prompt_version"])


def test_reference_scenarios_preserve_pre_audit_inputs_and_check_hash(tmp_path):
    import hashlib
    import pytest
    from eval.run_eval import load_reference_scenarios
    entry = next(d for d in json.loads(GOLD_MANIFEST.read_text())["documents"] if d["id"] == "atlassian")
    original = next(s for s in load_reference_scenarios(entry, GOLD_MANIFEST.parent) if s.id == "jira-sept-claim")
    assert "authorized_requester" not in original.account.prerequisites
    assert scenario("jira-sept-claim").account.prerequisites["authorized_requester"] is True
    path = tmp_path / "cases.json"
    path.write_text(json.dumps([original.model_dump(mode="json")]))
    local = {"scenario_snapshot": {"path": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}}
    assert load_reference_scenarios(local, tmp_path)[0] == original
    path.write_text("[]")
    with pytest.raises(ValueError, match="Changed reference scenario"):
        load_reference_scenarios(local, tmp_path)


def test_committed_eval_report_is_current(tmp_path):
    import subprocess, sys
    import pytest
    from shark.pipeline import ROOT
    if not (ROOT / ".cache/research/atlassian.html").exists():
        pytest.skip("The committed report was made with the private raw pages cached")
    out = tmp_path / "report.json"
    subprocess.run([sys.executable, "-m", "eval.run_eval", "--output", str(out)], cwd=ROOT, check=True, capture_output=True)
    assert json.loads(out.read_text()) == json.loads((ROOT / "data/gold/eval-report.json").read_text())
