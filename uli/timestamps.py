"""Timestamp grammars observed across the datasets in docs/research.md §8, plus dateutil fallback.
Every function is total: returns None instead of raising."""
from __future__ import annotations

import re
from datetime import datetime, timezone, timedelta

from dateutil import parser as du_parser
from dateutil import tz as du_tz

_YEAR_NOW = datetime.now(timezone.utc).year

_MON = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}

_GRAMMARS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})(?:[.,](\d{1,9}))?(Z|[+-]\d{2}:?\d{2})?"), "iso"),
    (re.compile(r"([A-Za-z]{3})\s+(\d{1,2}) (\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?(?: (\d{4}))?"), "bsd"),
    (re.compile(r"\[?([A-Za-z]{3}) ([A-Za-z]{3}) (\d{1,2}) (\d{2}):(\d{2}):(\d{2}) (\d{4})\]?"), "apache_err"),
    (re.compile(r"(\d{2})/([A-Za-z]{3})/(\d{4}):(\d{2}):(\d{2}):(\d{2}) ([+-]\d{4})"), "clf"),
    (re.compile(r"(\d{4})(\d{2})(\d{2})-(\d{2}):(\d{2}):(\d{2}):(\d{1,3})"), "healthapp"),
    (re.compile(r"(\d{2})-(\d{2}) (\d{2}):(\d{2}):(\d{2})\.(\d{3})"), "android"),
    (re.compile(r"(\d{2})\.(\d{2})\.(\d{2}) (\d{2}):(\d{2}):(\d{2})"), "proxifier"),
    (re.compile(r"\b(1[5-9]\d{8}|2\d{9})(?:\.(\d{1,6}))?\b"), "epoch_s"),
    (re.compile(r"\b(1[5-9]\d{11}|2\d{12})\b"), "epoch_ms"),
    (re.compile(r"(\d{4})(\d{2})(\d{2}) (\d{2})(\d{2})(\d{2})"), "compact"),
]


def _tzinfo(s: str | None):
    if not s or s == "Z":
        return timezone.utc
    sign = 1 if s[0] == "+" else -1
    hh = int(s[1:3])
    mm = int(s[-2:])
    return timezone(sign * timedelta(hours=hh, minutes=mm))


def parse_timestamp(text: str, *, max_scan: int = 96) -> tuple[datetime | None, str | None, str | None]:
    """Return (datetime_utc, grammar_name, original_text). Scans only the first `max_scan` chars
    (timestamps are overwhelmingly at line start); falls back to a whole-line ISO search."""
    head = text[:max_scan]
    for pat, name in _GRAMMARS:
        m = pat.search(head)
        if not m:
            continue
        dt = _build(name, m)
        if dt is not None:
            return dt, name, m.group(0)
    # fallback: ISO anywhere
    m = _GRAMMARS[0][0].search(text)
    if m:
        dt = _build("iso", m)
        if dt is not None:
            return dt, "iso", m.group(0)
    return None, None, None


def _build(name: str, m: re.Match[str]) -> datetime | None:
    try:
        g = m.groups()
        if name == "iso":
            frac = (g[6] or "0")[:6].ljust(6, "0")
            return datetime(int(g[0]), int(g[1]), int(g[2]), int(g[3]), int(g[4]), int(g[5]), int(frac), tzinfo=_tzinfo(g[7])).astimezone(timezone.utc)
        if name == "bsd":
            mon = _MON.get(g[0].lower())
            if not mon:
                return None
            year = int(g[6]) if g[6] else _YEAR_NOW
            frac = (g[5] or "0")[:6].ljust(6, "0")
            return datetime(year, mon, int(g[1]), int(g[2]), int(g[3]), int(g[4]), int(frac), tzinfo=timezone.utc)
        if name == "apache_err":
            mon = _MON.get(g[1].lower())
            if not mon:
                return None
            return datetime(int(g[6]), mon, int(g[2]), int(g[3]), int(g[4]), int(g[5]), tzinfo=timezone.utc)
        if name == "clf":
            mon = _MON.get(g[1].lower())
            if not mon:
                return None
            return datetime(int(g[2]), mon, int(g[0]), int(g[3]), int(g[4]), int(g[5]), tzinfo=_tzinfo(g[6])).astimezone(timezone.utc)
        if name == "healthapp":
            return datetime(int(g[0]), int(g[1]), int(g[2]), int(g[3]), int(g[4]), int(g[5]), int(g[6].ljust(3, "0")) * 1000, tzinfo=timezone.utc)
        if name == "android":
            return datetime(_YEAR_NOW, int(g[0]), int(g[1]), int(g[2]), int(g[3]), int(g[4]), int(g[5]) * 1000, tzinfo=timezone.utc)
        if name == "proxifier":
            return datetime(2000 + int(g[0]), int(g[1]), int(g[2]), int(g[3]), int(g[4]), int(g[5]), tzinfo=timezone.utc)
        if name == "epoch_s":
            return datetime.fromtimestamp(float(g[0] + ("." + g[1] if g[1] else "")), tz=timezone.utc)
        if name == "epoch_ms":
            return datetime.fromtimestamp(int(g[0]) / 1000.0, tz=timezone.utc)
        if name == "compact":
            return datetime(int(g[0]), int(g[1]), int(g[2]), int(g[3]), int(g[4]), int(g[5]), tzinfo=timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None
    return None


def parse_any(value: str) -> datetime | None:
    """Strict-ish dateutil fallback for values already isolated as a timestamp field."""
    if not value or len(value) > 64:
        return None
    dt, _, _ = parse_timestamp(value, max_scan=64)
    if dt:
        return dt
    try:
        d = du_parser.parse(value, fuzzy=False)
    except (ValueError, OverflowError, TypeError):
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc)


def to_epoch_ms(dt: datetime | None) -> int | None:
    if dt is None:
        return None
    return int(dt.timestamp() * 1000)
