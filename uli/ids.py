"""ULID generator (time-ordered, 26 chars, Crockford base32). No external dependency."""
from __future__ import annotations

import os
import time

_ENC = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def ulid(ts_ms: int | None = None) -> str:
    t = int(time.time() * 1000) if ts_ms is None else ts_ms
    rnd = int.from_bytes(os.urandom(10), "big")
    v = (t << 80) | rnd
    out = []
    for _ in range(26):
        out.append(_ENC[v & 31])
        v >>= 5
    return "".join(reversed(out))
