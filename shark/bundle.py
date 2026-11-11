"""Portable evidence and an offline verifier. No archive code is executed."""
import argparse
import gzip
import hashlib
import io
import json
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from shark.engine import evaluate
from shark.engine.metrics import apply_logs
from shark.models import Scenario
from shark.pipeline import ROOT
from shark.storage import utcnow

MAX_BYTES = 100_000_000
MAX_ARCHIVE = 30_000_000


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded(value):
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()


def engine_files():
    paths = [ROOT / "shark/models.py", ROOT / "shark/extract/citations.py", *sorted((ROOT / "shark/engine").glob("*.py"))]
    return {str(p.relative_to(ROOT)): digest(p.read_bytes()) for p in paths}


def build_bundle(store, s, claim=None):
    s = s.model_copy(deep=True)
    files, missing = {}, []
    for incident in s.incidents:
        if not incident.log:
            continue
        path = ROOT / incident.log.path
        if not path.is_file():
            raise ValueError("A request log is missing. Restore the source evidence before exporting.")
        raw = path.read_bytes()
        if digest(raw) != incident.log.sha256:
            raise ValueError("A request log changed. Review the evidence before exporting.")
        name = f"logs/{incident.log.sha256}.csv" + (".gz" if path.suffix == ".gz" else "")
        files[name] = raw
        incident.log.path = name
    # Re-measure from the exact exported bytes, not a cached interval summary.
    with tempfile.TemporaryDirectory(prefix="shark-export-") as temp:
        for name, raw in files.items():
            target = Path(temp) / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
        s = apply_logs(s, Path(temp))
        decision = evaluate(s)
    files.update({"scenario.json": encoded(s.model_dump(mode="json")), "decision.json": encoded(decision.model_dump(mode="json")),
                  "agreement.txt": s.source_text.encode(), "terms.json": encoded(s.terms.model_dump(mode="json"))})
    if claim:
        files["claim.json"] = encoded(claim)
        files["draft.txt"] = (claim["subject"] + "\n\n" + claim["body"]).encode()
    monthly = store.record("monthly_case", s.id)
    document_ids = set()
    if monthly:
        files["case-inputs.json"] = encoded(monthly)
        read = store.get_read(monthly["read_id"])
        if read:
            files["agreement-review.json"] = encoded(read)
            document_ids.add(read.get("source", {}).get("document_id"))
        document_ids.update([monthly.get("invoice_id"), monthly.get("receiving_invoice_id")])
        for evidence_id in monthly["evidence_ids"] + monthly["ticket_ids"]:
            item = store.record("evidence", evidence_id)
            raw = store.attachment(evidence_id)
            if not item or not raw or digest(raw) != item["sha256"]:
                raise ValueError("A selected evidence record is missing or changed. Restore it before exporting.")
            files[f"evidence/{evidence_id}.json"] = encoded(item)
            files[f"evidence/{evidence_id}." + ("csv" if item["kind"] == "logs" else "txt")] = raw
    else:
        missing.append("Original invoice PDFs and ticket exports are not linked to this case; its supplied fields remain in scenario.json.")
    for document_id in sorted(x for x in document_ids if x):
        doc, raw = store.record("document", document_id), store.attachment(document_id)
        if not doc or not raw or digest(raw) != doc["sha256"]:
            raise ValueError("A linked PDF is missing or changed. Restore it before exporting.")
        files[f"documents/{document_id}.pdf"] = raw
        files[f"documents/{document_id}.json"] = encoded(doc)
    files["README.txt"] = ("Refund Shark evidence bundle\n\nFrom the matching repository checkout, run:\n"
        "  uv run python -m shark.bundle verify /path/to/claim.zip\n\n"
        "The verifier uses the local repository engine, never code from the archive. It checks hashes, remeasures the logs, "
        "and replays the calculation at the saved evaluation clock without network access. Run make setup beforehand.\n"
        "Hashes establish internal consistency, not authenticity of a contract or customer evidence. A matching result is not vendor approval. "
        "The bundle contains only this case's selected evidence; keep private customer data private.\n\n" + "\n".join(missing)).encode()
    if len(files) > 250 or sum(map(len, files.values())) > MAX_BYTES:
        raise ValueError("This evidence bundle is too large. Limit the selected source exports before downloading.")
    manifest = {"format": "refund-shark-evidence-v1", "created_at": utcnow(), "case_id": s.id, "simulated": s.simulated,
                "evaluated_at": s.as_of.isoformat(), "engine_files": engine_files(), "limitations": missing,
                "files": {name: {"sha256": digest(raw), "bytes": len(raw)} for name, raw in files.items()}}
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", encoded(manifest))
        for name, raw in files.items():
            archive.writestr(name, raw)
    raw = buffer.getvalue()
    if len(raw) > MAX_ARCHIVE:
        raise ValueError("The compressed bundle exceeds 30 MB. Reduce selected evidence before downloading.")
    return raw


def verify_bundle(raw):
    if len(raw) > MAX_ARCHIVE:
        raise ValueError("Archive exceeds the 30 MB limit.")
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            info = archive.infolist()
            names = [i.filename for i in info]
            if len(info) > 251 or len(names) != len(set(names)) or sum(i.file_size for i in info) > MAX_BYTES:
                raise ValueError("Unsafe archive: too many files, duplicate names, or excessive size.")
            if any(PurePosixPath(n).is_absolute() or ".." in PurePosixPath(n).parts or "\\" in n or ":" in n for n in names):
                raise ValueError("Unsafe archive paths.")
            if any((i.external_attr >> 16) & 0o170000 == 0o120000 for i in info):
                raise ValueError("Archive symbolic links are not allowed.")
            manifest = json.loads(archive.read("manifest.json"))
            if manifest["format"] != "refund-shark-evidence-v1" or set(names) != set(manifest["files"]) | {"manifest.json"}:
                raise ValueError("Archive contents do not match the manifest.")
            if manifest["engine_files"] != engine_files():
                raise ValueError("Engine version differs. Use the repository version that exported this bundle.")
            files = {}
            for name, expected in manifest["files"].items():
                value = archive.read(name)
                if len(value) != expected["bytes"] or digest(value) != expected["sha256"]:
                    raise ValueError(f"Evidence integrity check failed: {name}")
                files[name] = value
            s = Scenario.model_validate_json(files["scenario.json"])
            if s.source_text.encode() != files["agreement.txt"] or s.terms.model_dump(mode="json") != json.loads(files["terms.json"]):
                raise ValueError("Agreement evidence disagrees with the scenario.")
            with tempfile.TemporaryDirectory(prefix="shark-verify-") as temp:
                expanded = 0
                for i in s.incidents:
                    if not i.log:
                        continue
                    name = i.log.path
                    if name not in files or not name.startswith("logs/") or digest(files[name]) != i.log.sha256:
                        raise ValueError("Scenario refers to a missing or inconsistent log.")
                    if name.endswith(".gz"):
                        with gzip.GzipFile(fileobj=io.BytesIO(files[name])) as compressed:
                            content = compressed.read(MAX_BYTES + 1)
                        expanded += len(content)
                        if expanded > MAX_BYTES:
                            raise ValueError("Expanded logs exceed the 100 MB limit.")
                    target = Path(temp) / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(files[name])
                computed = evaluate(apply_logs(s, Path(temp))).model_dump(mode="json")
            if computed != json.loads(files["decision.json"]):
                raise ValueError("The saved decision does not match the calculation replay.")
            return {"verified": True, "case_id": s.id, "files_checked": len(files), "simulated": s.simulated,
                    "outcome": computed["outcome"], "amount": computed["amount"], "currency": computed["currency"],
                    "evaluated_at": computed["evaluated_at"], "limitations": manifest["limitations"],
                    "note": "File integrity and calculation reproduced. Source authenticity and vendor acceptance are not verified."}
    except (KeyError, TypeError, OSError, EOFError, zipfile.BadZipFile, RuntimeError) as error:
        raise ValueError("Invalid evidence archive or missing required files.") from error


def main():
    parser = argparse.ArgumentParser(description="Verify a Refund Shark evidence bundle offline")
    parser.add_argument("command", choices=["verify"])
    parser.add_argument("archive", type=Path)
    args = parser.parse_args()
    try:
        result = verify_bundle(args.archive.read_bytes())
    except (ValueError, OSError) as error:
        print(f"Verification failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
