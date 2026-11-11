"""Minimal RFC 5545 calendar events, so a claim deadline can live in the team's calendar."""
from datetime import datetime, timezone


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _fold(line: str) -> list[str]:
    """Content lines are at most 75 octets; continuation lines start with a space."""
    out, current = [], ""
    for char in line:
        if len((current + char).encode()) > (75 if not out else 74):
            out.append(current)
            current = char
        else:
            current += char
    out.append(current)
    return [out[0]] + [" " + part for part in out[1:]]


def ics_event(uid: str, at: datetime, summary: str, description: str, url: str | None = None, reminder: str = "-P1D") -> str:
    stamp = lambda value: value.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")  # noqa: E731
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Refund Shark//Claim deadlines//EN", "CALSCALE:GREGORIAN", "BEGIN:VEVENT",
             f"UID:{uid}", f"DTSTAMP:{stamp(datetime.now(timezone.utc))}", f"DTSTART:{stamp(at)}",
             f"SUMMARY:{_escape(summary)}", f"DESCRIPTION:{_escape(description)}"] + ([f"URL:{url}"] if url else []) + [
             "BEGIN:VALARM", "ACTION:DISPLAY", f"TRIGGER:{reminder}", f"DESCRIPTION:{_escape(summary)}", "END:VALARM", "END:VEVENT", "END:VCALENDAR"]
    return "".join(part + "\r\n" for line in lines for part in _fold(line))
