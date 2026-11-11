import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from shark.engine import evaluate
from decimal import Decimal

from shark.engine.metrics import apply_logs, downtime_from_samples, read_log
from shark.extract.citations import source_hash
from shark.extract.excerpts import load_manifest, missing_excerpt_lines
from shark.extract.fetch import fetch_snapshot, html_to_text
from shark.extract.provider import extract, replay
from shark.incidents.statuspage import fetch_feed
from shark.models import Scenario
from shark.pipeline import ROOT, load_scenarios


def parse_as_of(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise argparse.ArgumentTypeError("--as-of needs a time zone, e.g. 2026-10-29T12:00:00Z")
    return result


def select(args) -> list[Scenario]:
    if args.input:
        return [apply_logs(Scenario.model_validate_json(args.input.read_text()))]
    scenarios = load_scenarios()
    if not any([args.vendor, args.product, args.plan, args.period]):
        chosen = [s for s in scenarios if s.id == (args.scenario or "jira-sept-claim")]
    else:
        wanted = {"vendor": args.vendor, "product": args.product, "plan": args.plan, "period": args.period}
        chosen = [s for s in scenarios if (not args.scenario or s.id == args.scenario)
                  and all(value is None or getattr(s, key).lower() == value.lower() for key, value in wanted.items())]
    if not chosen:
        available = "; ".join(f"{s.id} ({s.vendor}/{s.product}/{s.plan}/{s.period})" for s in scenarios)
        raise ValueError(f"No bundled scenario matches that selection. Available: {available}")
    return chosen


def verify_sources(cache_root: Path = ROOT / ".cache/sources") -> dict:
    """Check committed excerpts against their recorded snapshot and the newest `fetch-sources` download.

    The recorded raw page is private to the machine that fetched it; a fresh download lets anyone
    confirm that the excerpt lines still appear in the vendor's published text, or see that they changed.
    """
    results = []
    for ref in load_manifest()["sources"]:
        excerpt = (ROOT / ref["excerpt_path"]).read_text()
        result = {"id": ref["id"], "excerpt_intact": source_hash(excerpt) == ref["excerpt_sha256"], "snapshot": "not_cached"}
        raw = ROOT / ref["cache_path"]
        if raw.exists():
            if hashlib.sha256(raw.read_bytes()).hexdigest() != ref["raw_sha256"]:
                result["snapshot"] = "raw_hash_differs"
            else:
                text = html_to_text(raw.read_text())
                missing = missing_excerpt_lines(excerpt, text)
                result["snapshot"] = "verified" if source_hash(text) == ref["text_sha256"] and not missing else "text_differs"
                result["missing_lines"] = len(missing)
        fetched = sorted((cache_root / Path(ref["cache_path"]).stem).glob("*/source.html"))
        if fetched:
            raw_bytes = fetched[-1].read_bytes()
            missing = missing_excerpt_lines(excerpt, html_to_text(raw_bytes.decode("utf-8", errors="replace")))
            result["latest_fetch"] = {"retrieved": fetched[-1].parent.name, "raw_matches_recorded": hashlib.sha256(raw_bytes).hexdigest() == ref["raw_sha256"],
                                      "missing_excerpt_lines": len(missing)}
        results.append(result)
    from shark.spend import ramp  # the Ramp API reference lines the spend import cites
    results.append(ramp.verify_excerpt())
    return {"sources": results}


def main(argv: list[str] | None = None):
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser(prog="shark", description="Refund Shark — local service-credit evidence tools")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="Evaluate bundled scenarios (by id, or by vendor/product/plan/period)")
    run.add_argument("--scenario", help="Scenario id (default: jira-sept-claim when no other filter is given)")
    run.add_argument("--vendor")
    run.add_argument("--product")
    run.add_argument("--plan")
    run.add_argument("--period", help="Billing period, YYYY-MM")
    run.add_argument("--input", type=Path, help="Evaluate a complete Scenario JSON instead of a bundled one")
    clock = run.add_mutually_exclusive_group()
    clock.add_argument("--as-of", type=parse_as_of, help="Evaluation time (ISO 8601 with zone). Default: each scenario's labeled replay clock")
    clock.add_argument("--current", action="store_true", help="Evaluate at the real current time")
    extract_cmd = sub.add_parser("extract", help="Extract a local SLA text snapshot (requires a model key)")
    extract_cmd.add_argument("source", type=Path)
    extract_cmd.add_argument("--service", required=True)
    extract_cmd.add_argument("--plan", required=True)
    extract_cmd.add_argument("--fresh", action="store_true", help="Make a new paid call instead of using the cache")
    extract_cmd.add_argument("--output", type=Path)
    replay_cmd = sub.add_parser("replay", help="Validate a cached extraction against its exact source")
    replay_cmd.add_argument("extraction", type=Path)
    replay_cmd.add_argument("source", type=Path)
    fetch = sub.add_parser("fetch-sources", help="Fetch the official public source inventory")
    fetch.add_argument("--id", help="Fetch just one source ID")
    sub.add_parser("verify-sources", help="Check committed agreement excerpts against their recorded snapshots")
    logs = sub.add_parser("logs", help="Compute Downtime Minutes from a per-minute request log (minute,experience,requests,errors)")
    logs.add_argument("log", type=Path, help="CSV or .csv.gz")
    logs.add_argument("--threshold", type=Decimal, default=Decimal(5), help="A minute counts when the error rate is greater than this percentage")
    logs.add_argument("--experiences", help="Comma-separated covered experiences; default: every experience in the log")
    watch = sub.add_parser("watch", help="Poll vendor status pages and record alerts for the customers each outage could owe")
    watch.add_argument("--interval", type=int, default=120, help="Seconds between checks")
    watch.add_argument("--once", action="store_true", help="Check once and exit")
    feeds = sub.add_parser("fetch-incidents", help="Save a dated public Statuspage feed")
    feeds.add_argument("--host", default="jira-software.status.atlassian.com")
    args = parser.parse_args(argv)
    try:
        if args.command == "run":
            now = datetime.now(timezone.utc) if args.current else args.as_of
            results = []
            for scenario in select(args):
                decision = evaluate(scenario, now)
                results.append({"scenario": scenario.id, "customer": scenario.account.display_name, "role": scenario.role,
                                "as_of": decision.evaluated_at.isoformat(), "clock": "replay" if now is None else "given",
                                "decision": decision.model_dump(mode="json")})
            print(json.dumps({"mode": "bundled_demo_scenarios" if not args.input else "supplied_scenario", "results": results}, indent=2))
        elif args.command == "extract":
            result = extract(args.source.read_text(), args.service, args.plan, ROOT / ".cache/extractions", fresh=args.fresh)
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(json.dumps(result, indent=2) + "\n")
            print(json.dumps(result, indent=2))
        elif args.command == "replay":
            print(json.dumps(replay(args.extraction, args.source.read_text()), indent=2))
        elif args.command == "fetch-sources":
            sources = json.loads((ROOT / "docs/research/sla-catalog.json").read_text())["sources"]
            chosen = [s for s in sources if not args.id or s["id"] == args.id]
            if not chosen:
                raise ValueError("Unknown source ID")
            for source in chosen:
                print(json.dumps(fetch_snapshot(source["url"], ROOT / ".cache/sources", source["id"])))
                if source.get("supplemental_url"):
                    print(json.dumps(fetch_snapshot(source["supplemental_url"], ROOT / ".cache/sources", source["supplemental_id"])))
        elif args.command == "logs":
            samples = read_log(args.log)
            covered = [e.strip() for e in args.experiences.split(",")] if args.experiences else sorted({s.experience for s in samples})
            result = downtime_from_samples(samples, args.threshold, covered)
            print(json.dumps({"downtime_minutes": result.downtime_minutes, "minutes_covered": result.minutes_covered, "rows": result.rows,
                              "zero_request_minutes": result.zero_request_minutes, "below_threshold_error_minutes": result.below_threshold_error_minutes,
                              "excluded_error_minutes": result.excluded_error_minutes, "covered_experiences": covered,
                              "intervals": [{"start": i.start.isoformat().replace("+00:00", "Z"), "end": i.end.isoformat().replace("+00:00", "Z")}
                                            for i in result.intervals]}, indent=2))
        elif args.command == "verify-sources":
            report = verify_sources()
            print(json.dumps(report, indent=2))
            # A changed committed excerpt, or a cached snapshot that no longer matches its record, fails the check (and CI).
            if any(not s["excerpt_intact"] or s["snapshot"] in {"raw_hash_differs", "text_differs"} for s in report["sources"]):
                raise SystemExit(1)
        elif args.command == "watch":
            import os
            import time
            import shark.incidents.live as live
            from shark.incidents.watcher import Watcher
            from shark.storage import Store
            watcher = Watcher(Store(os.environ.get("SHARK_DB", str(ROOT / ".cache/refund-shark.sqlite3"))), load_scenarios(),
                              get=lambda url, timeout: live.http_get(url, timeout))
            while True:
                print(json.dumps(watcher.poll(), indent=2), flush=True)
                if args.once:
                    break
                time.sleep(args.interval)
        elif args.command == "fetch-incidents":
            print(json.dumps(fetch_feed(args.host, ROOT / ".cache/incidents"), indent=2))
    except (ValueError, OSError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
