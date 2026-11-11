"""Compare reviewed references while keeping AI assistance separate from human attestation."""
import argparse
import json
from pathlib import Path

from eval.run_eval import GOLD_MANIFEST, compare_fields, load_reference_scenarios, load_source
from shark.engine import evaluate
from shark.extract.citations import source_hash, term_status, validate_rules
from shark.models import SLATerms


def prepare():
    manifest = json.loads(GOLD_MANIFEST.read_text())
    entry = next(d for d in manifest["documents"] if d["id"] == "atlassian")
    source = load_source(entry, GOLD_MANIFEST.parent)
    terms = json.loads((GOLD_MANIFEST.parent / entry["gold_path"]).read_text())
    return {"document": entry["id"], "source_hash": source_hash(source), "reviewer": "", "reviewed_at": "",
        "confirmed_human_review": False, "source_urls": [entry["url"], entry["supplement"]["url"]],
        "instructions": "Read the complete frozen source and each proposed value. Correct terms and quotes, then mark every field reviewed. Do not mark model output as human-reviewed without a person's review.",
        "fields_reviewed": {name: False for name in SLATerms.model_fields}, "terms": terms,
        "note": "These are machine-drafted starting labels, not ground truth. Model runs are already frozen; this is a post-hoc reference review, not a new held-out experiment."}, source


def score(review):
    if not review.get("confirmed_human_review") or not str(review.get("reviewer", "")).strip() or not review.get("reviewed_at"):
        raise ValueError("A person must review the labels and record their name, date, and confirmation before scoring.")
    if set(review.get("fields_reviewed", {})) != set(SLATerms.model_fields) or any(v is not True for v in review["fields_reviewed"].values()):
        raise ValueError("Every reference field must be explicitly reviewed.")
    return {"mode": "post_hoc_human_reference_review", "reviewer": review["reviewer"], "reviewed_at": review["reviewed_at"],
        **_compare_reference(review),
        "limitation": "One agreement, three saved model runs, and four scenarios. Post-hoc review, not a new held-out evaluation or general accuracy benchmark. Reviewer identity is self-attested."}


def audit(review):
    """Diagnostic comparison against AI-checked labels; never a human-accuracy score."""
    ai = review.get("ai_review", {})
    if review.get("confirmed_human_review") is not False:
        raise ValueError("An AI audit must explicitly leave human review unconfirmed.")
    if not ai.get("reviewer") or not ai.get("reviewed_at") or set(ai.get("fields_reviewed", {})) != set(SLATerms.model_fields) or any(v is not True for v in ai["fields_reviewed"].values()):
        raise ValueError("Record the AI reviewer, date, and all 16 checked fields before auditing.")
    result = _compare_reference(review)
    for run in result["runs"]:
        run["material_field_agreement"] = run.pop("field_accuracy")
    result["worst_material_field_agreement"] = result.pop("worst_field_agreement")
    return {"mode": "post_hoc_ai_assisted_reference_audit", "confirmed_human_review": False,
        "reviewer": ai["reviewer"], "reviewed_at": ai["reviewed_at"], **result,
        "limitation": "AI-checked proposed labels, not human-reviewed ground truth or extraction accuracy. One agreement and four existing scenarios; both-needs-review matches do not demonstrate a priced decision. Exact field comparisons include implementation-specific exclusion labels and machine-checkability flags. Saved runs were visible during this post-hoc audit."}


def _compare_reference(review):
    if review.get("document") != "atlassian":
        raise ValueError("This worksheet evaluates the main Atlassian Jira workflow only.")
    entry = next(d for d in json.loads(GOLD_MANIFEST.read_text())["documents"] if d["id"] == "atlassian")
    source = load_source(entry, GOLD_MANIFEST.parent)
    if source_hash(source) != review["source_hash"]:
        raise ValueError("The reviewed reference and frozen source do not match.")
    gold = SLATerms.model_validate(review["terms"])
    if any(s == "unverified" for s in term_status(gold, source).values()) or validate_rules(gold):
        raise ValueError("Reference quotes or rules fail validation. Correct the worksheet before scoring.")
    cases = {s.id: s for s in load_reference_scenarios(entry, GOLD_MANIFEST.parent)}
    runs = []
    for path in entry["extraction_paths"]:
        run = json.loads((GOLD_MANIFEST.parent / path).read_text())
        if run["source_hash"] != review["source_hash"]:
            raise ValueError("A saved model run used a different source.")
        extracted = SLATerms.model_validate(run["terms"])
        decisions = []
        for id in entry["scenario_ids"]:
            case = cases[id].model_copy(deep=True)
            case.source_text, case.source_hash = source, review["source_hash"]
            case.terms = gold
            expected = evaluate(case)
            case.terms = extracted
            actual = evaluate(case)
            decisions.append({"case": id, "reference_outcome": expected.outcome, "model_outcome": actual.outcome,
                "reference_amount": str(expected.amount), "model_amount": str(actual.amount),
                "both_need_review": expected.outcome == actual.outcome == "needs_review",
                "agreement": (expected.outcome, expected.amount) == (actual.outcome, actual.amount),
                "false_positive": actual.outcome == "eligible" and expected.outcome != "eligible"})
        runs.append({"run": run["run"], **compare_fields(gold, extracted), "decisions": decisions})
    decisions = [d for run in runs for d in run["decisions"]]
    return {"document": "atlassian",
        "scenario_snapshot_sha256": entry.get("scenario_snapshot", {}).get("sha256"),
        "source_hash": review["source_hash"], "runs": runs, "worst_field_agreement": min(r["field_accuracy"] for r in runs),
        "false_positive_decisions": sum(d["false_positive"] for d in decisions),
        "decision_comparisons": len(decisions), "both_need_review": sum(d["both_need_review"] for d in decisions),
        "conclusive_decision_matches": sum(d["agreement"] and not d["both_need_review"] for d in decisions)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare")
    prep.add_argument("--output", type=Path, default=Path(".cache/label-review"))
    scoring = sub.add_parser("score")
    scoring.add_argument("review", type=Path)
    auditing = sub.add_parser("audit", help="AI-assisted diagnostic comparison; not a human review")
    auditing.add_argument("review", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            review, source = prepare()
            args.output.mkdir(parents=True, exist_ok=True)
            target = args.output / "atlassian-review.json"
            if target.exists():
                raise ValueError("A review already exists here. Choose another output directory to preserve the person's work.")
            target.write_text(json.dumps(review, indent=2) + "\n")
            (args.output / "frozen-source.txt").write_text(source)
            print(f"Prepared {target}. Human review is pending. The frozen source is alongside it.")
        else:
            compare = audit if args.command == "audit" else score
            print(json.dumps(compare(json.loads(args.review.read_text())), indent=2))
    except (OSError, ValueError) as error:
        parser.exit(1, f"Reference review unavailable: {error}\n")


if __name__ == "__main__":
    main()
