import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo


CLINIC_TIMEZONE = os.getenv("CLINIC_TIMEZONE", "Africa/Nairobi")


def clinic_timezone() -> ZoneInfo:
    return ZoneInfo(CLINIC_TIMEZONE)


def utc_now() -> datetime:
    """Return the current time as a naive UTC datetime.

    datetime.utcnow() is deprecated in Python 3.12+; this helper uses the
    recommended timezone-aware route and strips the tzinfo to keep the
    existing naive-UTC convention used throughout the app.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def as_utc(dt: datetime) -> datetime:
    """Convert a client datetime into a naive UTC datetime for storage.

    Naive datetimes are interpreted as clinic-local time (configured by
    CLINIC_TIMEZONE, default Africa/Nairobi). Offset-aware datetimes are
    accepted and converted to UTC. This gives the API a single fixed timezone
    at the edge while keeping the storage/comparison layer UTC.
    """
    if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
        dt = dt.replace(tzinfo=clinic_timezone())
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def from_utc(dt: datetime) -> datetime:
    """Convert a stored naive UTC datetime to a naive clinic-local datetime.

    Responses stay naive (no offset) so the client sees the same convention as
    the input: datetimes are in the clinic's configured timezone.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(clinic_timezone()).replace(tzinfo=None)
