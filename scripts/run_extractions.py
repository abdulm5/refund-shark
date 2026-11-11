"""Run live extractions for the evaluation manifest, with a hard spending stop.

Usage:
  .venv/bin/python scripts/run_extractions.py --only atlassian --runs 1   # one probe call
  .venv/bin/python scripts/run_extractions.py --split tuning               # 3 runs per tuning document
  .venv/bin/python scripts/run_extractions.py --freeze                     # freeze prompt, schema, and model
  .venv/bin/python scripts/run_extractions.py --split held_out             # one held-out batch, after freezing

Outputs land in data/extractions/<id>/run<k>.json and are listed in data/gold/manifest.json.
Existing outputs are kept and skipped, so an interrupted batch resumes. Requires ANTHROPIC_API_KEY.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

from eval.run_eval import GOLD_MANIFEST, load_source  # noqa: E402
from shark.extract.provider import DEFAULT_MODEL, EFFORT, PROMPT_VERSION, extract, schema_hash  # noqa: E402

# USD per million input / output tokens. Unknown models are priced high so the stop stays conservative.
PRICES = {"claude-sonnet-5-5": (2.0, 10.0), "claude-opus-5-5": (4.0, 20.0), "claude-haiku-5-5": (0.1, 0.5)}
FIRST_CALL_ESTIMATE = 0.25


def cost(result: dict) -> float:
    rate_in, rate_out = PRICES.get(result.get("served_by") or result.get("model"), (10.0, 50.0))
    usage = result.get("usage", {})
    return (usage.get("input_tokens", 0) * rate_in + usage.get("output_tokens", 0) * rate_out) / 1_000_000


def run_batch(manifest_path: Path, out_dir: Path, *, split: str | None = None, only: list[str] | None = None, runs: int = 3,
              budget: float = 4.5, extractor=None) -> dict:
    manifest = json.loads(manifest_path.read_text())
    base = manifest_path.parent
    docs = [d for d in manifest["documents"] if (not split or d["split"] == split) and (not only or d["id"] in only)]
    if any(d["split"] == "held_out" for d in docs):
        frozen = manifest["frozen_config"]
        if not frozen.get("frozen"):
            raise SystemExit("Freeze the configuration (--freeze) after tuning, before any held-out run.")
        if extractor is None and (frozen.get("prompt_version"), frozen.get("schema_hash"), frozen.get("model")) != (
                PROMPT_VERSION, schema_hash(), os.environ.get("SHARK_MODEL", DEFAULT_MODEL)):
            raise SystemExit("The prompt, schema, or model changed since freezing; held-out runs would not test the frozen configuration.")
    extractor = extractor or (lambda source, service, plan, cache_dir, run: extract(source, service, plan, cache_dir, run=run))
    spent, largest, stopped = 0.0, FIRST_CALL_ESTIMATE, False
    for doc in docs:
        source = load_source(doc, base)
        paths = doc.setdefault("extraction_paths", [])
        for run in range(1, runs + 1):
            target = out_dir / doc["id"] / f"run{run}.json"
            relative = os.path.relpath(target, base)
            if target.exists():
                if relative not in paths:
                    paths.append(relative)
                continue
            if spent + largest > budget:
                stopped = True
                break
            result = extractor(source, doc["selection"]["service"], doc["selection"]["plan"], ROOT / ".cache/extractions", run)
            call = cost(result)
            spent, largest = spent + call, max(largest if spent else 0, call)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps({**result, "cost_usd_estimate": round(call, 4), "split": doc["split"]}, indent=2) + "\n")
            paths.append(relative)
            manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")  # durable after every paid call
            print(f"{doc['id']} run {run}: {result.get('served_by')} · ${call:.3f} · total ${spent:.3f}", flush=True)
        if stopped:
            break
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    return {"spent_usd": spent, "stopped_for_budget": stopped}


def freeze(manifest_path: Path):
    manifest = json.loads(manifest_path.read_text())
    if not any(d.get("extraction_paths") for d in manifest["documents"] if d["split"] == "tuning"):
        raise SystemExit("Run the tuning documents before freezing.")
    manifest["frozen_config"] = {"frozen": True, "model": os.environ.get("SHARK_MODEL", DEFAULT_MODEL), "prompt_version": PROMPT_VERSION,
                                 "schema_hash": schema_hash(), "effort": EFFORT, "frozen_at": datetime.now(timezone.utc).isoformat()}
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest["frozen_config"], indent=2))


def main():
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--split", choices=["tuning", "held_out"])
    parser.add_argument("--only", nargs="+", help="Document ids")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--budget", type=float, default=4.5, help="Stop before total estimated spend in this invocation would exceed this many USD")
    parser.add_argument("--freeze", action="store_true")
    args = parser.parse_args()
    if args.freeze:
        return freeze(GOLD_MANIFEST)
    if not args.split and not args.only:
        raise SystemExit("Choose --split or --only.")
    report = run_batch(GOLD_MANIFEST, ROOT / "data/extractions", split=args.split, only=args.only, runs=args.runs, budget=args.budget)
    print(json.dumps(report))


if __name__ == "__main__":
    main()
