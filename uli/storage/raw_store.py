"""Append-only, content-addressed raw store. Raw is truth (docs/architecture.md P2).

Layout: <raw_dir>/<tenant>/<source_slug>/<yyyy-mm-dd>/<NNNNNN>.ndjson[.gz]
Each line: {"raw_event_id","received_at","source_id","transport","peer","payload_b64","len"}
Rotation: when the active segment exceeds max_bytes → close, optionally gzip (new file), then remove
the plain file only after the .gz has been re-read and hash-verified. Offsets in the index are always
offsets into the *uncompressed* byte stream, so reads work identically for .ndjson and .ndjson.gz.
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import io
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

import orjson

from uli.models import RawLocation, RawRecord

_SLUG = re.compile(r"[^A-Za-z0-9._:-]+")


def _slug(s: str) -> str:
    return _SLUG.sub("_", s)[:120] or "_"


class RawStore(Protocol):
    def append(self, rec: RawRecord) -> RawLocation: ...
    def read(self, loc: RawLocation) -> bytes: ...
    def read_record(self, loc: RawLocation) -> dict: ...
    def flush(self) -> None: ...


class _Segment:
    __slots__ = ("path", "fh", "size", "day", "no")

    def __init__(self, path: Path, day: str, no: int):
        self.path = path
        self.day = day
        self.no = no
        path.parent.mkdir(parents=True, exist_ok=True)
        self.fh = open(path, "ab", buffering=1 << 16)
        self.size = path.stat().st_size


class LocalRawStore:
    def __init__(self, root: Path, max_segment_bytes: int = 64 * 1024 * 1024, compress: bool = True):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_bytes = max_segment_bytes
        self.compress = compress
        self._segments: dict[tuple[str, str], _Segment] = {}
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- write
    def append(self, rec: RawRecord) -> RawLocation:
        day = rec.received_at.astimezone(timezone.utc).strftime("%Y-%m-%d")
        key = (rec.tenant_id, rec.source_id)
        line = orjson.dumps(
            {
                "raw_event_id": rec.raw_event_id,
                "received_at": rec.received_at.isoformat(),
                "source_id": rec.source_id,
                "transport": rec.transport,
                "peer": rec.peer,
                "payload_b64": base64.b64encode(rec.payload).decode("ascii"),
                "len": len(rec.payload),
            }
        ) + b"\n"
        with self._lock:
            seg = self._segments.get(key)
            if seg is None or seg.day != day or seg.size + len(line) > self.max_bytes:
                seg = self._rotate(key, rec.tenant_id, rec.source_id, day, seg)
            offset = seg.size
            seg.fh.write(line)
            seg.size += len(line)
            rel = str(seg.path.relative_to(self.root))
        return RawLocation(segment=rel, offset=offset, length=len(line))

    def _rotate(self, key, tenant: str, source: str, day: str, old: _Segment | None) -> _Segment:
        if old is not None:
            old.fh.flush()
            os.fsync(old.fh.fileno())
            old.fh.close()
            if self.compress:
                self._compress(old.path)
        d = self.root / _slug(tenant) / _slug(source) / day
        d.mkdir(parents=True, exist_ok=True)
        existing = sorted(p for p in d.iterdir() if p.name.endswith((".ndjson", ".ndjson.gz")))
        no = 1
        if existing:
            last = existing[-1].name.split(".")[0]
            no = int(last) + (1 if old is not None or existing[-1].name.endswith(".gz") else 0)
            if no == 0:
                no = 1
        path = d / f"{no:06d}.ndjson"
        seg = _Segment(path, day, no)
        self._segments[key] = seg
        return seg

    @staticmethod
    def _compress(path: Path) -> None:
        gz = path.with_suffix(".ndjson.gz")
        h1 = hashlib.sha256()
        with open(path, "rb") as src, gzip.open(gz, "wb", compresslevel=6) as dst:
            while chunk := src.read(1 << 20):
                h1.update(chunk)
                dst.write(chunk)
        h2 = hashlib.sha256()
        with gzip.open(gz, "rb") as chk:
            while chunk := chk.read(1 << 20):
                h2.update(chunk)
        if h1.digest() == h2.digest():
            path.unlink()
        else:  # keep the plain file; never lose raw
            gz.unlink(missing_ok=True)

    def flush(self) -> None:
        with self._lock:
            for seg in self._segments.values():
                seg.fh.flush()

    # ---------------------------------------------------------------- read
    def _open(self, segment: str):
        p = self.root / segment
        if p.exists():
            return open(p, "rb")
        gz = p.with_suffix(".ndjson.gz") if not segment.endswith(".gz") else p
        if gz.exists():
            return gzip.open(gz, "rb")
        raise FileNotFoundError(segment)

    def read_record(self, loc: RawLocation) -> dict:
        self.flush()
        with self._open(loc.segment) as fh:
            fh.seek(loc.offset)
            line = fh.read(loc.length)
        return orjson.loads(line)

    def read(self, loc: RawLocation) -> bytes:
        rec = self.read_record(loc)
        return base64.b64decode(rec["payload_b64"])

    def verify(self, loc: RawLocation, expected_sha256: str) -> bool:
        return hashlib.sha256(self.read(loc)).hexdigest() == expected_sha256

    def close(self) -> None:
        with self._lock:
            for seg in self._segments.values():
                try:
                    seg.fh.flush()
                    seg.fh.close()
                except Exception:
                    pass
            self._segments.clear()
