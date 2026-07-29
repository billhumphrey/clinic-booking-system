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


DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

_DAY_OF_WEEK_ALIASES: dict[str, int] = {}
for i, name in enumerate(DAY_NAMES):
    lower = name.lower()
    _DAY_OF_WEEK_ALIASES[lower] = i
    _DAY_OF_WEEK_ALIASES[lower + "s"] = i  # plural, e.g. "mondays"
    _DAY_OF_WEEK_ALIASES[lower[:3]] = i    # e.g. "mon", "tue"
    _DAY_OF_WEEK_ALIASES[lower[:2]] = i    # e.g. "mo", "tu"

# Common non-first-three-letter short forms
_DAY_OF_WEEK_ALIASES["tues"] = 1
_DAY_OF_WEEK_ALIASES["thurs"] = 3
_DAY_OF_WEEK_ALIASES["thur"] = 3

# Unambiguous single-letter abbreviations
_DAY_OF_WEEK_ALIASES["m"] = 0
_DAY_OF_WEEK_ALIASES["w"] = 2
_DAY_OF_WEEK_ALIASES["f"] = 4


def parse_day_of_week(value: int | str) -> int:
    """Convert a day name, abbreviation, or 0-6 number to a weekday index."""
    if isinstance(value, int):
        if 0 <= value <= 6:
            return value
        raise ValueError(f"Invalid day_of_week number: {value}")

    s = str(value).strip().lower()
    if s in _DAY_OF_WEEK_ALIASES:
        return _DAY_OF_WEEK_ALIASES[s]

    if s.isdigit():
        n = int(s)
        if 0 <= n <= 6:
            return n
        raise ValueError(f"Invalid day_of_week number: {value}")

    raise ValueError(f"Invalid day_of_week: {value}")


def format_day_of_week(day_index: int) -> str:
    """Convert a 0-6 weekday index to its full name."""
    if 0 <= day_index <= 6:
        return DAY_NAMES[day_index]
    raise ValueError(f"Invalid day_of_week number: {day_index}")
