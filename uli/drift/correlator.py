"""Hypothesis H2: correlate parser-drift events with resource-drift events in a look-back window."""
from __future__ import annotations

from datetime import timedelta, timezone


def _aware(dt):
    """SQLite round-trips DateTime(timezone=True) columns back as naive datetimes (a documented
    SQLAlchemy/SQLite gotcha -- the driver has no real timezone type), while `pdrift.detected_at`
    is a fresh in-memory tz-aware value; comparing the two raised TypeError on every call, which
    the caller's broad except silently swallowed. Assume UTC for anything that comes back naive,
    matching how every detected_at is produced (models.utcnow())."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def correlate(storage, pdrift, window_seconds: float) -> list[str]:
    since = pdrift.detected_at - timedelta(seconds=window_seconds)
    candidates = storage.list_drift(pdrift.tenant_id, since=since, limit=50)
    pd_at = _aware(pdrift.detected_at)
    explained_by = [c["drift_id"] for c in candidates if _aware(c["detected_at"]) <= pd_at]
    explained_by.sort(key=lambda did: _aware(next(c["detected_at"] for c in candidates if c["drift_id"] == did)), reverse=True)
    return explained_by[:5]
