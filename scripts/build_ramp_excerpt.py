"""Cut the committed Ramp API excerpt from the privately cached public docs.

Usage: .venv/bin/python scripts/build_ramp_excerpt.py
Requires the documents listed in DOCS under .cache/research/ramp/, fetched from docs.ramp.com.
Fails rather than writing anything if a cached file's hash differs from the recorded retrieval.
Each block keeps the section's title and path plus the exact lines shark/spend/ramp.py cites.
"""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shark.extract.citations import FRAGMENT_MARKER, source_hash  # noqa: E402
from shark.spend.ramp import CITED, EXCERPT, RECORD  # noqa: E402

CACHE = ROOT / ".cache/research/ramp"
DOCS = {
    "api": {"url": "https://docs.ramp.com/llms-api.txt", "file": "llms-api.txt", "retrieved_at": "2026-10-31T23:19:58Z",
            "sha256": "6af9481568b68f2d5ccd2b3cff46477e9aa75431490cae171a3149a3f049df8c"},
    "Quickstart": {"url": "https://docs.ramp.com/llms-guides/getting-started.txt", "file": "getting-started.txt", "retrieved_at": "2026-10-31T23:24:02Z",
                   "sha256": "581a32b166ecf67a51a57a8a9f4299eb23ebd4bfb4143f9fb9d90254f6fcf542"},
    "Pagination": {"url": "https://docs.ramp.com/llms-guides/pagination.txt", "file": "pagination.txt", "retrieved_at": "2026-10-31T23:24:02Z",
                   "sha256": "4d0bf6972de8445e20a453c41f36e08db8ae4880dfea7d17fe1782e42b58f1f0"},
    "Authorization": {"url": "https://docs.ramp.com/llms-guides/authorization.txt", "file": "authorization.txt", "retrieved_at": "2026-10-31T23:24:02Z",
                      "sha256": "7d5375e5313532bf0d2dda04e17f03acbf4167ea3ba9abc7384a00db8c36b8fb"},
}


def text(doc: dict) -> str:
    raw = (CACHE / doc["file"]).read_bytes()
    if hashlib.sha256(raw).hexdigest() != doc["sha256"]:
        raise SystemExit(f"{doc['file']} differs from the recorded retrieval; refusing to rebuild the excerpt")
    return raw.decode()


def section_lines(block: str) -> tuple[list[str], str]:
    """The lines a block may quote from: an API reference section (title and path), or a whole guide."""
    if " · " in block:
        title, path = block.split(" · ")
        for section in text(DOCS["api"]).split("\n---\n"):
            lines = section.strip().splitlines()
            if lines[:2] == [f"title: {title}", f"path: {path}"]:
                return lines, DOCS["api"]["url"]
        raise SystemExit(f"No section {block}")
    return text(DOCS[block]).splitlines(), DOCS[block]["url"]


def main():
    out = []
    for block in dict.fromkeys(b for b, _ in CITED):
        lines, url = section_lines(block)
        wanted = {line for (b, _), line in CITED.items() if b == block}
        keep = [i for i, line in enumerate(lines) if line.strip() in wanted or (" · " in block and i < 2)]
        missing = wanted - {lines[i].strip() for i in keep}
        if missing:
            raise SystemExit(f"{block}: not in the source: {missing}")
        out.append(f"## {block} | {url}")
        previous = None
        for i in sorted(set(keep)):
            if previous is not None and i != previous + 1:
                out.append(FRAGMENT_MARKER)
            out.append(lines[i].strip())
            previous = i
        out.append("")
    excerpt = "\n".join(out)
    EXCERPT.write_text(excerpt)
    RECORD.write_text(json.dumps({"note": "Verbatim lines of Ramp's public Developer API reference that the spend import relies on. "
                                          "Each '## block | url' header is ours; every other line is copied from that document.",
                                  "documents": [{"id": key, **{k: v for k, v in doc.items() if k != 'file'}, "cache_path": f".cache/research/ramp/{doc['file']}"}
                                                for key, doc in DOCS.items()],
                                  "excerpt_path": str(EXCERPT.relative_to(ROOT)), "excerpt_sha256": source_hash(excerpt)}, indent=2) + "\n")
    print(f"{len(out)} lines")


if __name__ == "__main__":
    main()
