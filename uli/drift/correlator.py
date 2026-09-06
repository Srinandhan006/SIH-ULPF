"""Hypothesis H2: correlate parser-drift events with resource-drift events in a look-back window."""
from __future__ import annotations

from datetime import timedelta


def correlate(storage, pdrift, window_seconds: float) -> list[str]:
    since = pdrift.detected_at - timedelta(seconds=window_seconds)
    candidates = storage.list_drift(pdrift.tenant_id, since=since, limit=50)
    explained_by = [c["drift_id"] for c in candidates if c["detected_at"] <= pdrift.detected_at]
    explained_by.sort(key=lambda did: next(c["detected_at"] for c in candidates if c["drift_id"] == did), reverse=True)
    return explained_by[:5]
