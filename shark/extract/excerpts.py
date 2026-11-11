"""Bounded, verbatim excerpts of public agreements that can be committed and re-verified.

The full vendor pages stay in an ignored cache. An excerpt keeps whole lines of the parsed
snapshot text, in order, with omitted runs marked by a line containing only the marker.
"""
import json
from pathlib import Path

from .citations import FRAGMENT_MARKER

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "data/sources/manifest.json"


def load_manifest(path: Path = MANIFEST) -> dict:
    return json.loads(path.read_text())


def missing_excerpt_lines(excerpt: str, snapshot: str) -> list[str]:
    """Return excerpt lines that are not verbatim lines of the snapshot text."""
    available = set(snapshot.splitlines())
    return [line for line in excerpt.splitlines() if line.strip() and line != FRAGMENT_MARKER and line not in available]


def compose_source(ids: list[str], manifest: dict | None = None) -> tuple[str, list[dict]]:
    """Join excerpts under authored headings; the result is what quotes are verified against."""
    manifest = manifest or load_manifest()
    refs = {ref["id"]: ref for ref in manifest["sources"]}
    parts, used = [], []
    for source_id in ids:
        ref = refs[source_id]
        parts.append(f"=== {ref['title']} — verbatim excerpt ({ref['url']}) ===\n\n{(ROOT / ref['excerpt_path']).read_text().strip()}")
        used.append({key: ref[key] for key in ("id", "title", "url", "retrieved_at", "effective_date", "raw_sha256", "text_sha256", "excerpt_sha256", "parser")})
    return "\n\n".join(parts) + "\n", used
