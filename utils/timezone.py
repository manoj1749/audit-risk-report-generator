"""IST timestamp helper.

Cloud Run containers run in UTC regardless of the deployed region, and
`zoneinfo.ZoneInfo` can't be relied on here since the slim base image has
no IANA tzdata installed. IST has no DST, so a fixed UTC+5:30 offset is
exact and doesn't need either.
"""
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))


def now_ist() -> datetime:
    return datetime.now(IST)


def to_ist(dt: datetime) -> datetime:
    """Convert a timezone-aware datetime (e.g. GCS blob time_created, which
    is UTC) to IST. A naive datetime is assumed to already be UTC."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(IST)


def format_ist(dt: datetime) -> str:
    return to_ist(dt).strftime("%d %b %Y %H:%M IST")
