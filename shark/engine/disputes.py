"""Business-day windows for asking a vendor to revisit an awarded credit.

Atlassian: "If you disagree with the amount awarded, you have 3 business days to request that we
revisit the claim." The procedure names no time zone or holiday calendar, so days count Monday to
Friday with no holidays, from the moment the award is recorded, across every civil time zone (as
for the claim deadline). The UTC reading is reported alongside.
"""
from datetime import datetime, time, timedelta, timezone


def end_of_business_day(start: datetime, days: int, zone) -> datetime:
    day = start.astimezone(zone).date()
    counted = 0
    while counted < days:
        day += timedelta(days=1)
        if day.weekday() < 5:
            counted += 1
    return (datetime.combine(day + timedelta(days=1), time(0), tzinfo=zone) - timedelta(microseconds=1)).astimezone(timezone.utc)


# Every civil offset from UTC-12 to UTC+14, in quarter hours.
ZONES = [timezone(timedelta(minutes=m)) for m in range(-12 * 60, 14 * 60 + 1, 15)]


def revisit_window(recorded: datetime, days: int) -> tuple[datetime, datetime, datetime]:
    """(earliest, UTC, latest) last instants to ask for a revisit.

    The award's local date differs by zone and can straddle a weekend, so neither end zone is always the
    extreme: the envelope is taken over every civil offset."""
    readings = [end_of_business_day(recorded, days, zone) for zone in ZONES]
    return min(readings), end_of_business_day(recorded, days, timezone.utc), max(readings)
