from datetime import datetime, timezone


def utc_now() -> datetime:
    """Return the current time as a naive UTC datetime.

    datetime.utcnow() is deprecated in Python 3.12+; this helper uses the
    recommended timezone-aware route and strips the tzinfo to keep the
    existing naive-UTC convention used throughout the app.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)
