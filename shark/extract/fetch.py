import hashlib
import io
import ipaddress
import json
import re
import socket
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
import pdfplumber
import trafilatura

from .citations import source_hash

PARSER = f"trafilatura {trafilatura.__version__} (txt, tables, links)"


def html_to_text(html: str) -> str:
    """The one HTML parser for snapshots and excerpts, so a raw file always yields the same text."""
    return trafilatura.extract(html, include_tables=True, output_format="txt", include_links=True) or ""


def effective_date(text: str) -> str | None:
    match = re.search(r"Effective (?:starting|date)?:?\s*([A-Z][a-z]+ \d{1,2}, \d{4})", text)
    return datetime.strptime(match.group(1), "%B %d, %Y").date().isoformat() if match else None


def save_snapshot(content: bytes, content_type: str, url: str, output_dir: Path, source_id: str, retrieved_at: datetime) -> dict:
    if not source_id.replace("-", "").replace("_", "").isalnum():
        raise ValueError("Invalid source identifier")
    is_pdf = "application/pdf" in content_type
    if is_pdf:
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            text = "\n\n".join(page.extract_text() or "" for page in pdf.pages)
        raw_text, parser = text, "pdfplumber"
    else:
        raw_text = content.decode("utf-8", errors="replace")
        text, parser = html_to_text(raw_text), PARSER
    if not text.strip():
        raise ValueError(f"No readable text found at {url}")
    folder = output_dir / source_id / retrieved_at.strftime("%Y%m%dT%H%M%SZ")
    folder.mkdir(parents=True, exist_ok=True)
    (folder / ("source.pdf" if is_pdf else "source.html")).write_bytes(content)
    (folder / "source.txt").write_text(text)
    metadata = {"id": source_id, "url": url, "retrieved_at": retrieved_at.isoformat(), "raw_sha256": hashlib.sha256(content).hexdigest(),
                "source_hash": source_hash(text), "effective_date": effective_date(raw_text), "parser": parser, "text_path": str(folder / "source.txt")}
    (folder / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    return metadata


NAT64 = [ipaddress.ip_network("64:ff9b::/96"), ipaddress.ip_network("64:ff9b:1::/48")]


def public_address(address: str) -> bool:
    """Globally routable unicast only: no private, loopback, link-local, multicast, or NAT64-translated addresses."""
    ip = ipaddress.ip_address(address.split("%")[0])
    return ip.is_global and not ip.is_multicast and not (ip.version == 6 and any(ip in net for net in NAT64))


def public_url(url: str) -> str:
    """Accept only https links whose host resolves to public addresses: no localhost, private ranges, or metadata IPs."""
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("Use an https:// link to a public page.")
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)}
    except (socket.gaierror, UnicodeError):
        raise ValueError("That address could not be resolved.") from None
    if not addresses or any(not public_address(a) for a in addresses):
        raise ValueError("Only public websites can be read.")
    return url


def fetch_public(url: str, max_bytes: int = 5_000_000) -> tuple[bytes, str, str]:
    """Download a user-supplied page, re-checking every redirect hop and capping the size."""
    for _ in range(4):
        public_url(url)
        with httpx.stream("GET", url, timeout=20, follow_redirects=False, headers={"User-Agent": "RefundSharkResearch/0.1"}) as response:
            if response.is_redirect:
                url = urljoin(url, response.headers["location"])
                continue
            response.raise_for_status()
            chunks, size = [], 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > max_bytes:
                    raise ValueError("That page is larger than 5 MB.")
                chunks.append(chunk)
            return b"".join(chunks), response.headers.get("content-type", ""), url
    raise ValueError("Too many redirects.")


def fetch_snapshot(url: str, output_dir: Path, source_id: str) -> dict:
    """Fetch a catalog source from the CLI. Links pasted into the app go through fetch_public instead."""
    response = httpx.get(url, timeout=30, follow_redirects=True, headers={"User-Agent": "RefundSharkResearch/0.1"})
    response.raise_for_status()
    metadata = save_snapshot(response.content, response.headers.get("content-type", ""), url, output_dir, source_id, datetime.now(timezone.utc))
    return {**metadata, "final_url": str(response.url)}
