"""Build the committed Atlassian excerpts from the privately cached snapshots.

Usage: .venv/bin/python scripts/build_source_excerpts.py
Requires the raw HTML recorded in docs/research/source-snapshots.json under .cache/research/.
Fails rather than writing anything if a raw file's hash differs from the recorded retrieval.
"""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shark.extract.citations import FRAGMENT_MARKER, source_hash  # noqa: E402
from shark.extract.excerpts import MANIFEST  # noqa: E402
from shark.extract.fetch import PARSER, effective_date, html_to_text  # noqa: E402

# (first line prefix, last line prefix), inclusive, searched in document order.
SOURCES = {
    "atlassian-sla": {
        "snapshot": "atlassian",
        "title": "Atlassian Service Level Agreement",
        "ranges": [("For Eligible Cloud Products", "99.95%"), ("2. Service Credits", "2.2. Issuance."),
                   ("3. Exclusions", "Customer is not entitled"), ("Appendix A", "View Board"),
                   ("Notes: * Except for Bitbucket", "Notes: * Except for Bitbucket"), ("Appendix B", "Notes: * Percentage"),
                   ("Calculation", "The monthly uptime percentage indicated"), ("Definitions", "“Error Rate” means")],
    },
    "atlassian-compensation": {
        "snapshot": "atlassian-compensation",
        "title": "Atlassian Support: Service Level Agreement for Atlassian cloud apps",
        "ranges": [("Process to get compensation", "You can only request SLA compensation"),
                   ("You can submit a request for an app", "- Site admin for the app"),
                   ("To submit a request:", "If multiple incidents occur"),
                   ("Receive compensation", "After submitting your request"),
                   ("If your bill is on monthly payments", "If you pay your bill annually")],
    },
}


def select(lines: list[str], ranges: list[tuple[str, str]]) -> str:
    out, cursor = [], 0
    for first, last in ranges:
        start = next(i for i in range(cursor, len(lines)) if lines[i].startswith(first))
        end = next(i for i in range(start, len(lines)) if lines[i].startswith(last))
        if out or start > 0:
            out.append(FRAGMENT_MARKER)
        out.extend(lines[start:end + 1])
        cursor = end + 1
    if cursor < len(lines):
        out.append(FRAGMENT_MARKER)
    return "\n".join(out) + "\n"


def main():
    recorded = {s["id"]: s for s in json.loads((ROOT / "docs/research/source-snapshots.json").read_text())}
    entries = []
    for excerpt_id, spec in SOURCES.items():
        snap = recorded[spec["snapshot"]]
        raw_path = ROOT / snap["raw_path"]
        raw = raw_path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != snap["sha256"]:
            raise SystemExit(f"{raw_path} differs from the recorded retrieval; refusing to rebuild excerpts")
        html = raw.decode("utf-8", errors="replace")
        text = html_to_text(html)
        excerpt = select(text.splitlines(), spec["ranges"])
        path = ROOT / "data/sources" / f"{excerpt_id}.excerpt.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(excerpt)
        entries.append({"id": excerpt_id, "title": spec["title"], "url": snap["url"], "retrieved_at": snap["retrieved_at"],
                        "effective_date": effective_date(html), "raw_sha256": snap["sha256"], "cache_path": snap["raw_path"],
                        "parser": PARSER, "text_sha256": source_hash(text), "excerpt_path": str(path.relative_to(ROOT)),
                        "excerpt_sha256": source_hash(excerpt), "ranges": [list(r) for r in spec["ranges"]]})
        print(f"{excerpt_id}: {len(excerpt)} of {len(text)} characters")
    MANIFEST.write_text(json.dumps({"note": "Verbatim line excerpts of public pages, kept for citation checks. Raw pages stay in the ignored cache.",
                                    "sources": entries}, indent=2, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
