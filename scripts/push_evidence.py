"""Push a monitoring CSV or support-ticket export into a vendor's authenticated evidence inbox."""
import argparse
import json
import os
from pathlib import Path
from urllib.parse import urlparse, quote

import httpx
from dotenv import load_dotenv


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vendor", required=True, help="Vendor setup ID shown in the integration panel")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--period", help="UTC billing month, YYYY-MM; required for a CSV")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--csv", type=Path)
    group.add_argument("--ticket-json", type=Path)
    args = parser.parse_args()
    base = urlparse(args.base_url)
    if base.username or base.password or base.query or base.fragment or not (base.scheme == "https" or (base.scheme == "http" and base.hostname in {"127.0.0.1", "localhost", "::1"})):
        parser.error("Use HTTPS, or HTTP on localhost, without credentials in the URL.")
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    token = os.getenv("SHARK_EVIDENCE_TOKEN")
    if not token:
        parser.error("Set SHARK_EVIDENCE_TOKEN in the environment or local .env.")
    path = args.csv or args.ticket_json
    if path.stat().st_size > 8_000_000:
        parser.error("Export files must be under 8 MB.")
    if args.csv:
        if not args.period:
            parser.error("--period is required with --csv")
        body = {"kind": "logs", "label": path.name, "period": args.period, "csv": path.read_text()}
    else:
        body = json.loads(path.read_text())
        if body.get("kind") != "ticket":
            parser.error("The support export needs kind: ticket.")
    try:
        response = httpx.post(f"{args.base_url.rstrip('/')}/api/integrations/evidence/{quote(args.vendor, safe='')}",
            json=body, headers={"Authorization": f"Bearer {token}"}, timeout=45, follow_redirects=False)
        response.raise_for_status()
    except httpx.HTTPError:
        parser.exit(1, "Evidence import was not confirmed. Check the server, token, and payload; identical retries are safe.\n")
    result = response.json()
    print(json.dumps({"id": result["id"], "kind": result["kind"], "period": result["period"], "analysis": result.get("analysis")}, indent=2))


if __name__ == "__main__":
    main()
