"""Offline fixture checks and opt-in scoring of separately reviewed model outputs.

No live model calls are made by this evaluator. It never labels replay as extraction accuracy.
"""
import argparse
import hashlib
import json
from decimal import Decimal
from pathlib import Path

from shark.engine import evaluate
from shark.engine.metrics import apply_logs
from shark.extract.citations import source_hash, term_status
from shark.extract.fetch import html_to_text
from shark.models import SLATerms, Scenario
from shark.pipeline import ROOT, load_scenarios


GOLD_MANIFEST = ROOT / "data/gold/manifest.json"


def material(name: str, value):
    """What a decision depends on. Free-text descriptions and channel wording are compared by quote checks, not here."""
    if value is None:
        return None
    if name == "downtime_definition":
        return (value.kind, value.interval_seconds, value.error_threshold_pct, sorted(value.covered_experiences))
    if name == "exclusions":
        return sorted({(e.kind, e.machine_checkable) for e in value})
    if name == "claim_procedure":
        return (sorted(set(value.required_fields)), value.subject_line)
    if name in {"covered_services", "prerequisites"}:
        return sorted(set(value))
    return value


def compare_fields(gold: SLATerms, extracted: SLATerms) -> dict:
    # A confirmed absence and an empty extraction are different answers.
    fields = {name: (material(name, getattr(gold, name).value), getattr(gold, name).absent)
              == (material(name, getattr(extracted, name).value), getattr(extracted, name).absent) for name in SLATerms.model_fields}
    return {"fields": fields, "field_accuracy": sum(fields.values()) / len(fields), "credit_table_exact": gold.credit_tiers.value == extracted.credit_tiers.value}


def load_part(part: dict, base: Path, label: str) -> str:
    """Read one frozen source: plain text, or a recorded raw page parsed the same way as every snapshot."""
    if part.get("raw_path"):
        raw = (base / part["raw_path"]).read_bytes()
        if hashlib.sha256(raw).hexdigest() != part["raw_sha256"]:
            raise ValueError(f"Changed raw snapshot for {label}")
        text = html_to_text(raw.decode("utf-8", errors="replace"))
    else:
        text = (base / part["source_path"]).read_text()
    if source_hash(text) != part["source_hash"]:
        raise ValueError(f"Changed source for {label}")
    return text


def load_source(entry: dict, base: Path) -> str:
    """The model's input for a document: its page, plus a supplemental page when the agreement points to one."""
    text = load_part(entry, base, entry["id"])
    supplement = entry.get("supplement")
    if not supplement:
        return text
    extra = load_part(supplement, base, f"{entry['id']} supplement")
    parts = [(entry.get("title", entry["id"]), entry.get("url", ""), text), (supplement["title"], supplement["url"], extra)]
    return "\n\n".join(f"=== {title} — full text ({url}) ===\n\n{body.strip()}" for title, url, body in parts) + "\n"


def load_reference_scenarios(entry: dict, base: Path) -> list[Scenario]:
    """Keep historical decision comparisons stable when application fixtures are corrected."""
    snapshot = entry.get("scenario_snapshot")
    if snapshot is None:
        return load_scenarios()
    raw = (base / snapshot["path"]).read_bytes()
    if hashlib.sha256(raw).hexdigest() != snapshot["sha256"]:
        raise ValueError("Changed reference scenario snapshot")
    return [apply_logs(Scenario.model_validate(case)) for case in json.loads(raw)]


def term_signature(terms: SLATerms, name: str) -> str:
    """Runs agree on a term when its material value and absence agree; rewording a description is not disagreement."""
    field = getattr(terms, name)
    return json.dumps([material(name, field.value), field.absent], sort_keys=True, default=str)


def summarize_runs(manifest: dict, base: Path) -> list[dict]:
    """Label-free metrics for saved model runs, plus agreement with draft labels where they exist.

    Without human-reviewed gold this is not accuracy: quote checks show provenance, consistency shows
    stability across runs, and draft-label agreement only shows where the model and the drafter differ.
    """
    results = []
    for entry in manifest["documents"]:
        paths = entry.get("extraction_paths") or []
        if not paths:
            results.append({"id": entry["id"], "split": entry["split"], "status": "no_runs"})
            continue
        try:
            source = load_source(entry, base)
        except FileNotFoundError:
            source = None  # raw pages stay in the private cache; a clean clone or CI only has the saved runs
        runs = [json.loads((base / path).read_text()) for path in paths]
        terms = [SLATerms.model_validate(run["terms"]) for run in runs]
        if source is not None:
            input_hash = source_hash(source)
            if any(run["source_hash"] != input_hash for run in runs):
                raise ValueError(f"A saved run for {entry['id']} was made from a different source")
            statuses = [term_status(t, source) for t in terms]
        else:
            statuses = [run.get("term_status", {}) for run in runs]
        rates = []
        for status in statuses:
            stated = [s for s in status.values() if s in {"verified", "unverified"}]
            rates.append(stated.count("verified") / len(stated) if stated else 0)
        names = list(SLATerms.model_fields)
        inconsistent = [name for name in names if len({term_signature(t, name) for t in terms}) > 1]
        doc = {"id": entry["id"], "split": entry["split"], "status": "runs_saved" if source is not None else "runs_saved_source_not_cached", "runs": len(runs),
               "models": sorted({run.get("served_by") or run["model"] for run in runs}), "quote_match_rates": rates,
               "quote_match_rate_worst": min(rates), "unverified_terms": sorted({n for s in statuses for n, v in s.items() if v == "unverified"}),
               "terms_missing": [sum(v == "missing" for v in s.values()) for s in statuses],
               "terms_absent": [sum(v == "absent" for v in s.values()) for s in statuses],
               "run_consistency": (len(names) - len(inconsistent)) / len(names), "inconsistent_fields": inconsistent,
               "tokens": {"input": sum(run.get("usage", {}).get("input_tokens", 0) for run in runs),
                          "output": sum(run.get("usage", {}).get("output_tokens", 0) for run in runs)}}
        if entry.get("gold_path"):
            draft = SLATerms.model_validate_json((base / entry["gold_path"]).read_text())
            comparisons = [compare_fields(draft, t) for t in terms]
            decisions = []
            for scenario_id in entry.get("scenario_ids", []) if source is not None else []:
                for extracted in terms:
                    case = next(s for s in load_reference_scenarios(entry, base) if s.id == scenario_id).model_copy(deep=True)
                    case.source_text, case.source_hash = source, input_hash
                    case.terms = draft
                    expected = evaluate(case)
                    case.terms = extracted
                    actual = evaluate(case)
                    both_review = expected.outcome == actual.outcome == "needs_review"
                    decisions.append({"scenario": scenario_id, "expected": expected.outcome, "actual": actual.outcome,
                                      "match": (expected.outcome, expected.amount) == (actual.outcome, actual.amount) and not both_review,
                                      "both_review": both_review, "false_positive": actual.outcome == "eligible" and expected.outcome != "eligible"})
            doc["draft_label_agreement"] = {
                "review_status": entry.get("review_status"), "note": "Agreement with labels nobody has reviewed is not accuracy.",
                "worst_field_agreement": min(c["field_accuracy"] for c in comparisons),
                "disagreeing_fields": sorted({name for c in comparisons for name, same in c["fields"].items() if not same}),
                "decision_matches": sum(d["match"] for d in decisions), "both_review": sum(d["both_review"] for d in decisions),
                "false_positive_eligible": sum(d["false_positive"] for d in decisions), "decision_comparisons": len(decisions), "decisions": decisions,
                "decisions_skipped": None if source is not None else "Source page not cached here, so decisions were not re-run; quote statuses are those recorded with each run."}
        results.append(doc)
    return results


def score_suite(manifest: dict, base: Path) -> list[dict]:
    results = []
    for entry in manifest["documents"]:
        if entry.get("review_status") != "human_reviewed":
            reason = {"machine_drafted": "Draft gold labels exist but have not been reviewed by a person",
                      "not_labeled": "No gold labels yet"}.get(entry.get("review_status"), "Gold labels need human review")
            results.append({"id": entry["id"], "split": entry["split"], "status": "not_run", "reason": reason})
            continue
        if not manifest["frozen_config"].get("frozen"):
            raise ValueError("Freeze the prompt, schema, and model configuration before scoring")
        source = load_source(entry, base)
        gold = SLATerms.model_validate_json((base / entry["gold_path"]).read_text())
        paths = entry.get("extraction_paths", [])
        if len(paths) != 3:
            raise ValueError("Three independent saved extraction runs are required per document")
        samples = []
        for path in paths:
            saved = json.loads((base / path).read_text())
            if saved.get("mode") != "live" or saved["source_hash"] != source_hash(source):
                raise ValueError("Scored samples must be live outputs from the exact source snapshot")
            for field in ("model", "prompt_version", "schema_hash"):
                if saved.get(field) != manifest["frozen_config"][field]:
                    raise ValueError(f"Extraction {field} differs from the frozen evaluation configuration")
            if saved.get("served_by", saved["model"]) != saved["model"]:
                raise ValueError("A run answered by a fallback model is not a run of the frozen model")
            extracted = SLATerms.model_validate(saved["terms"])
            sample = compare_fields(gold, extracted)
            status = term_status(extracted, source)
            stated = [s for s in status.values() if s in {"verified", "unverified"}]
            sample["quote_match_rate"] = stated.count("verified") / len(stated) if stated else 0
            sample["terms_missing"] = sum(s == "missing" for s in status.values())
            sample["terms_absent"] = sum(s == "absent" for s in status.values())
            decisions = []
            for scenario_path in entry.get("scenario_paths", []):
                case = Scenario.model_validate_json((base / scenario_path).read_text())
                case.source_text, case.source_hash = source, source_hash(source)
                case.terms = gold
                expected = evaluate(case)
                case.terms = extracted
                actual = evaluate(case)
                both_review = expected.outcome == actual.outcome == "needs_review"
                decisions.append({"match": (expected.outcome, expected.amount) == (actual.outcome, actual.amount) and not both_review,
                                  "both_review": both_review,
                                  "false_positive": actual.outcome == "eligible" and expected.outcome != "eligible"})
            sample["decisions"] = decisions
            samples.append(sample)
        every = [d for s in samples for d in s["decisions"]]
        results.append({"id": entry["id"], "split": entry["split"], "status": "scored", "runs": samples,
                        "worst_field_accuracy": min(s["field_accuracy"] for s in samples),
                        "worst_quote_match_rate": min(s["quote_match_rate"] for s in samples),
                        "credit_table_exact_runs": sum(s["credit_table_exact"] for s in samples),
                        "priced_decision_matches": sum(d["match"] for d in every), "both_review_matches": sum(d["both_review"] for d in every),
                        "false_positive_eligible": sum(d["false_positive"] for d in every), "decision_comparisons": len(every)})
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=GOLD_MANIFEST)
    parser.add_argument("--output", type=Path, default=ROOT / ".cache/eval/latest.json")
    args = parser.parse_args()
    # Expected results were derived by hand from the quoted clauses, not from engine output.
    expected = {"jira-sept-claim": ("eligible", "900.00", "900.00"),
                "jira-sept-missing-evidence": ("needs_review", "0.00", "480.00"),
                "jira-sept-maintenance": ("not_eligible", "0.00", "0.00"),
                "jira-oct-degraded": ("needs_review", "0.00", None),
                "github": ("needs_review", "0.00", None), "cloudflare": ("needs_review", "0.00", None)}
    cases = []
    for case in load_scenarios():
        d = evaluate(case)
        if case.id not in expected:
            raise SystemExit(f"No hand-derived expectation for scenario {case.id}")
        outcome, amount, estimate = expected[case.id]
        cases.append({"id": case.id, "role": case.role, "outcome": d.outcome, "amount": str(d.amount), "provisional_estimate": None if d.estimated_amount is None else str(d.estimated_amount),
                      "expected_result_matches": d.outcome == outcome and d.amount == Decimal(amount)
                      and d.estimated_amount == (None if estimate is None else Decimal(estimate)),
                      "quotes_verified": sum(s == "verified" for s in d.term_status.values()),
                      "terms_confirmed_absent": sum(s == "absent" for s in d.term_status.values()), "terms": len(d.term_status)})
    results = {"offline_validation": {"label": "Authored fixture checks — not live extraction accuracy", "cases": cases},
               "live_extraction": None}
    manifest = json.loads(args.manifest.read_text())
    documents = score_suite(manifest, args.manifest.parent)
    scored = [d for d in documents if d["status"] == "scored"]
    runs = summarize_runs(manifest, args.manifest.parent)
    has_runs = any(d["status"].startswith("runs_saved") for d in runs)
    cached = any(d["status"] == "runs_saved" for d in runs)
    results["live_extraction"] = {
        "status": "scored" if scored else "runs_saved" if has_runs else "not_run",
        "label": "Live model extraction accuracy" if scored else (
            ("Model runs saved; accuracy not scored because no gold labels are human-reviewed. Quote checks and run consistency are label-free; "
             "agreement with draft labels is not accuracy." if cached else
             "Model runs saved; the raw vendor pages are not cached here, so quote checks are those recorded with each run and decisions were "
             "not re-run (data/gold/eval-report.json has the full report). No gold labels are human-reviewed.")
            if has_runs else "Not run: no human-reviewed gold labels and no live extraction outputs"),
        "model_runs": runs,
        "frozen_config": manifest["frozen_config"],
        "tuning_documents": sum(d["split"] == "tuning" for d in documents), "held_out_documents": sum(d["split"] == "held_out" for d in documents),
        "documents": documents}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps(results, indent=2))
    if not all(c["expected_result_matches"] for c in cases):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
