from datetime import datetime, timezone
from decimal import Decimal

from shark.models import Interval


def month_bounds(period: str) -> tuple[datetime, datetime]:
    start = datetime.strptime(period, "%Y-%m").replace(tzinfo=timezone.utc)
    end = start.replace(year=start.year + 1, month=1) if start.month == 12 else start.replace(month=start.month + 1)
    return start, end


def microseconds(start: datetime, end: datetime) -> int:
    delta = end - start
    return (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds


def merge(intervals: list[Interval]) -> list[Interval]:
    result: list[Interval] = []
    for item in sorted(intervals, key=lambda i: i.start):
        if result and item.start <= result[-1].end:
            result[-1] = Interval(start=result[-1].start, end=max(item.end, result[-1].end))
        else:
            result.append(item.model_copy())
    return result


def clip(intervals: list[Interval], start: datetime, end: datetime) -> list[Interval]:
    return [Interval(start=max(i.start, start), end=min(i.end, end)) for i in intervals if max(i.start, start) < min(i.end, end)]


def subtract(intervals: list[Interval], exclusions: list[Interval]) -> list[Interval]:
    result = merge(intervals)
    for exclusion in merge(exclusions):
        remaining = []
        for item in result:
            if exclusion.end <= item.start or exclusion.start >= item.end:
                remaining.append(item)
            else:
                if item.start < exclusion.start:
                    remaining.append(Interval(start=item.start, end=exclusion.start))
                if exclusion.end < item.end:
                    remaining.append(Interval(start=exclusion.end, end=item.end))
        result = remaining
    return result


def minutes(intervals: list[Interval]) -> Decimal:
    return Decimal(sum(microseconds(i.start, i.end) for i in merge(intervals))) / Decimal(60_000_000)
