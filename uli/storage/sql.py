"""SQL storage adapter (SQLite for local/demo, PostgreSQL for production) — one code path via SQLAlchemy."""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any

import orjson
from sqlalchemy import (JSON, Boolean, Column, DateTime, Float, Integer, MetaData, String, Table, Text, create_engine, event, func, select, text, update)
from sqlalchemy.engine import Engine

from uli.ids import ulid
from uli.metrics import STORAGE_LATENCY
from uli.models import DriftEvent, NormalizedEvent, ParserDriftEvent, RawLocation, RawRecord

md = MetaData()

raw_index = Table(
    "raw_index", md,
    Column("raw_event_id", String(64), primary_key=True),
    Column("tenant_id", String(64), primary_key=True),
    Column("source_id", String(256), index=True),
    Column("segment", String(512)), Column("offset", Integer), Column("length", Integer),
    Column("received_at", DateTime(timezone=True), index=True),
)
events = Table(
    "events", md,
    Column("event_id", String(26), primary_key=True),
    Column("tenant_id", String(64), index=True),
    Column("project_id", String(64)),
    Column("source_id", String(256), index=True),
    Column("ts", DateTime(timezone=True), index=True),
    Column("class_uid", Integer, index=True),
    Column("tier", Integer, index=True),
    Column("confidence", Float),
    Column("parser_id", String(128), index=True),
    Column("parser_version", String(32)),
    Column("raw_event_id", String(64), index=True),
    Column("template_id", String(64)),
    Column("shape_hash", String(16), index=True),
    Column("family_hash", String(16), index=True),
    Column("message", Text),
    Column("anomaly_score", Float),
    Column("body", JSON),
    Column("created_at", DateTime(timezone=True)),
)
sources = Table(
    "sources", md,
    Column("tenant_id", String(64), primary_key=True), Column("source_id", String(256), primary_key=True),
    Column("display_name", String(256)), Column("transport", String(32)),
    Column("first_seen", DateTime(timezone=True)), Column("last_seen", DateTime(timezone=True)),
    Column("event_count", Integer, default=0), Column("active_parser_id", String(128)),
    Column("parser_health", String(16), default="unknown"),
    Column("reference_histogram", JSON), Column("reference_captured_at", DateTime(timezone=True)),
)
parsers = Table(
    "parsers", md,
    Column("parser_id", String(128), primary_key=True),
    Column("version", String(32)), Column("kind", String(16)), Column("vendor", String(128)), Column("product", String(128)),
    Column("signatures", JSON), Column("compat", JSON), Column("status", String(16), index=True),
    Column("bundle_sha256", String(64)), Column("signature_ok", Boolean), Column("yaml", Text),
    Column("created_at", DateTime(timezone=True)), Column("promoted_at", DateTime(timezone=True)),
    Column("promoted_by", String(128)), Column("previous_version", String(32)),
)
fingerprints = Table(
    "fingerprints", md,
    Column("shape_hash", String(16), primary_key=True),
    Column("family_hash", String(16), index=True), Column("shape", Text), Column("example_raw_event_id", String(64)),
    Column("first_seen", DateTime(timezone=True)), Column("last_seen", DateTime(timezone=True)),
    Column("count", Integer, default=0), Column("routed_parser_id", String(128)),
)
unknown_clusters = Table(
    "unknown_clusters", md,
    Column("cluster_id", String(26), primary_key=True),
    Column("tenant_id", String(64), index=True), Column("source_id", String(256), index=True),
    Column("family_hash", String(16), index=True), Column("shape_hashes", JSON), Column("template_ids", JSON),
    Column("templates", JSON), Column("inferred_fields", JSON), Column("candidate_parsers", JSON),
    Column("confidence", Float), Column("status", String(16), index=True), Column("event_count", Integer),
    Column("example_raw_event_id", String(64)), Column("first_seen", DateTime(timezone=True)), Column("last_seen", DateTime(timezone=True)),
)
parser_suggestions = Table(
    "parser_suggestions", md,
    Column("suggestion_id", String(26), primary_key=True),
    Column("tenant_id", String(64)), Column("cluster_id", String(26)), Column("source_id", String(256)),
    Column("origin", String(16)), Column("base_parser_id", String(128)), Column("parser_id", String(128)),
    Column("yaml", Text), Column("score", Float), Column("status", String(16), index=True),
    Column("created_at", DateTime(timezone=True)), Column("details", JSON),
)
drift_events = Table(
    "drift_events", md,
    Column("drift_id", String(26), primary_key=True),
    Column("tenant_id", String(64), index=True), Column("kind", String(32), index=True), Column("key", String(256)),
    Column("desired", JSON), Column("observed", JSON), Column("severity", String(16)),
    Column("detected_at", DateTime(timezone=True), index=True), Column("snapshot_raw_event_id", String(64)),
    Column("desired_state_sha256", String(64)),
)
parser_drift_events = Table(
    "parser_drift_events", md,
    Column("pdrift_id", String(26), primary_key=True),
    Column("tenant_id", String(64), index=True), Column("source_id", String(256), index=True),
    Column("parser_id", String(128)), Column("parser_version", String(32)),
    Column("detected_at", DateTime(timezone=True), index=True), Column("js_divergence", Float),
    Column("confidence_before", Float), Column("confidence_after", Float),
    Column("reference_histogram", JSON), Column("current_histogram", JSON), Column("window_size", Integer),
    Column("explained_by", JSON), Column("suggestion_id", String(26)), Column("reason", Text),
)
audit = Table(
    "audit", md,
    Column("id", String(26), primary_key=True), Column("tenant_id", String(64)), Column("actor", String(128)),
    Column("action", String(64)), Column("target", String(256)), Column("at", DateTime(timezone=True)), Column("details", JSON),
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _row(r) -> dict:
    return dict(r._mapping) if r is not None else None


class SQLStorage:
    def __init__(self, url: str, echo: bool = False):
        self.url = url
        self.is_sqlite = url.startswith("sqlite")
        kw: dict[str, Any] = {"echo": echo, "future": True}
        if self.is_sqlite:
            kw["connect_args"] = {"check_same_thread": False, "timeout": 30}
        else:
            kw["pool_pre_ping"] = True
            kw["pool_size"] = 10
        self.engine: Engine = create_engine(url, **kw)
        if self.is_sqlite:
            @event.listens_for(self.engine, "connect")
            def _pragma(dbapi_conn, _):
                cur = dbapi_conn.cursor()
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA synchronous=NORMAL")
                cur.execute("PRAGMA busy_timeout=30000")
                cur.close()
        md.create_all(self.engine)
        self._fts_ok = False
        if self.is_sqlite:
            self._init_fts()
        self._lock = threading.Lock() if self.is_sqlite else None

    # ------------------------------------------------------------- helpers
    def _init_fts(self) -> None:
        try:
            with self.engine.begin() as c:
                c.execute(text("CREATE VIRTUAL TABLE IF NOT EXISTS events_fts USING fts5(event_id UNINDEXED, message)"))
            self._fts_ok = True
        except Exception:
            self._fts_ok = False

    def _begin(self):
        return self.engine.begin()

    # ------------------------------------------------------------- raw
    def index_raw(self, rec: RawRecord, loc: RawLocation) -> None:
        with STORAGE_LATENCY.labels("index_raw").time(), self._begin() as c:
            exists = c.execute(select(raw_index.c.raw_event_id).where(raw_index.c.raw_event_id == rec.raw_event_id, raw_index.c.tenant_id == rec.tenant_id)).first()
            if exists is None:
                c.execute(raw_index.insert().values(raw_event_id=rec.raw_event_id, tenant_id=rec.tenant_id, source_id=rec.source_id, segment=loc.segment, offset=loc.offset, length=loc.length, received_at=rec.received_at))

    def get_raw_location(self, tenant_id: str, raw_event_id: str):
        with self._begin() as c:
            r = c.execute(select(raw_index).where(raw_index.c.raw_event_id == raw_event_id, raw_index.c.tenant_id == tenant_id)).first()
        if r is None:
            return None
        d = _row(r)
        return RawLocation(segment=d["segment"], offset=d["offset"], length=d["length"]), d

    # ------------------------------------------------------------- events
    def write_event(self, ev: NormalizedEvent, *, shape_hash: str, family_hash: str) -> None:
        body = orjson.loads(ev.model_dump_json())
        ts_ms = ev.ocsf.get("time")
        ts = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc) if isinstance(ts_ms, (int, float)) else ev.provenance.received_at
        with STORAGE_LATENCY.labels("write_event").time(), self._begin() as c:
            c.execute(events.insert().values(
                event_id=ev.event_id, tenant_id=ev.tenant_id, project_id=ev.project_id, source_id=ev.source_id, ts=ts,
                class_uid=ev.ocsf.get("class_uid", 0), tier=ev.provenance.tier, confidence=ev.provenance.confidence,
                parser_id=ev.provenance.parser_id, parser_version=ev.provenance.parser_version,
                raw_event_id=ev.provenance.raw_event_id, template_id=ev.provenance.template_id,
                shape_hash=shape_hash, family_hash=family_hash, message=ev.message[:4000],
                anomaly_score=ev.anomaly.get("score"), body=body, created_at=_now()))
            if self._fts_ok:
                c.execute(text("INSERT INTO events_fts(event_id, message) VALUES (:e, :m)"), {"e": ev.event_id, "m": ev.message[:4000]})

    def write_events_bulk(self, rows: list[tuple[NormalizedEvent, str, str]]) -> None:
        if not rows:
            return
        vals = []
        fts = []
        for ev, shape_hash, family_hash in rows:
            body = orjson.loads(ev.model_dump_json())
            ts_ms = ev.ocsf.get("time")
            ts = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc) if isinstance(ts_ms, (int, float)) else ev.provenance.received_at
            vals.append(dict(event_id=ev.event_id, tenant_id=ev.tenant_id, project_id=ev.project_id, source_id=ev.source_id, ts=ts,
                             class_uid=ev.ocsf.get("class_uid", 0), tier=ev.provenance.tier, confidence=ev.provenance.confidence,
                             parser_id=ev.provenance.parser_id, parser_version=ev.provenance.parser_version,
                             raw_event_id=ev.provenance.raw_event_id, template_id=ev.provenance.template_id,
                             shape_hash=shape_hash, family_hash=family_hash, message=ev.message[:4000],
                             anomaly_score=ev.anomaly.get("score"), body=body, created_at=_now()))
            fts.append({"e": ev.event_id, "m": ev.message[:4000]})
        with STORAGE_LATENCY.labels("write_events_bulk").time(), self._begin() as c:
            c.execute(events.insert(), vals)
            if self._fts_ok:
                c.execute(text("INSERT INTO events_fts(event_id, message) VALUES (:e, :m)"), fts)

    def get_event(self, tenant_id: str, event_id: str) -> dict | None:
        with self._begin() as c:
            r = c.execute(select(events.c.body).where(events.c.event_id == event_id, events.c.tenant_id == tenant_id)).first()
        return r[0] if r else None

    def query_events(self, tenant_id: str, *, source_id: str | None = None, tier: int | None = None, class_uid: int | None = None,
                     parser_id: str | None = None, min_confidence: float | None = None, max_confidence: float | None = None,
                     q: str | None = None, since: datetime | None = None, until: datetime | None = None,
                     limit: int = 100, offset: int = 0, order: str = "desc") -> list[dict]:
        stmt = select(events.c.body).where(events.c.tenant_id == tenant_id)
        if source_id:
            stmt = stmt.where(events.c.source_id == source_id)
        if tier is not None:
            stmt = stmt.where(events.c.tier == tier)
        if class_uid is not None:
            stmt = stmt.where(events.c.class_uid == class_uid)
        if parser_id:
            stmt = stmt.where(events.c.parser_id == parser_id)
        if min_confidence is not None:
            stmt = stmt.where(events.c.confidence >= min_confidence)
        if max_confidence is not None:
            stmt = stmt.where(events.c.confidence <= max_confidence)
        if since:
            stmt = stmt.where(events.c.ts >= since)
        if until:
            stmt = stmt.where(events.c.ts <= until)
        if q:
            if self._fts_ok:
                stmt = stmt.where(events.c.event_id.in_(select(text("event_id")).select_from(text("events_fts")).where(text("events_fts MATCH :q"))))
                stmt = stmt.params(q=_fts_escape(q))
            else:
                stmt = stmt.where(events.c.message.ilike(f"%{q}%"))
        stmt = stmt.order_by(events.c.created_at.desc() if order == "desc" else events.c.created_at.asc()).limit(min(limit, 1000)).offset(offset)
        with self._begin() as c:
            return [r[0] for r in c.execute(stmt)]

    def count_events(self, tenant_id: str, **f: Any) -> int:
        stmt = select(func.count()).select_from(events).where(events.c.tenant_id == tenant_id)
        for k, v in f.items():
            if v is not None and hasattr(events.c, k):
                stmt = stmt.where(getattr(events.c, k) == v)
        with self._begin() as c:
            return int(c.execute(stmt).scalar() or 0)

    # ------------------------------------------------------------- sources
    def upsert_source(self, tenant_id: str, source_id: str, **fields: Any) -> None:
        now = _now()
        with self._begin() as c:
            r = c.execute(select(sources.c.event_count).where(sources.c.tenant_id == tenant_id, sources.c.source_id == source_id)).first()
            inc = fields.pop("_inc", 0)
            if r is None:
                c.execute(sources.insert().values(tenant_id=tenant_id, source_id=source_id, first_seen=now, last_seen=now, event_count=inc, display_name=fields.pop("display_name", source_id), **fields))
            else:
                vals = dict(last_seen=now, **fields)
                if inc:
                    vals["event_count"] = (r[0] or 0) + inc
                c.execute(update(sources).where(sources.c.tenant_id == tenant_id, sources.c.source_id == source_id).values(**vals))

    def get_source(self, tenant_id: str, source_id: str) -> dict | None:
        with self._begin() as c:
            return _row(c.execute(select(sources).where(sources.c.tenant_id == tenant_id, sources.c.source_id == source_id)).first())

    def list_sources(self, tenant_id: str) -> list[dict]:
        with self._begin() as c:
            return [_row(r) for r in c.execute(select(sources).where(sources.c.tenant_id == tenant_id).order_by(sources.c.last_seen.desc()))]

    # ------------------------------------------------------------- parsers
    def upsert_parser(self, **fields: Any) -> None:
        pid = fields["parser_id"]
        with self._begin() as c:
            r = c.execute(select(parsers.c.parser_id).where(parsers.c.parser_id == pid)).first()
            if r is None:
                c.execute(parsers.insert().values(created_at=_now(), **fields))
            else:
                c.execute(update(parsers).where(parsers.c.parser_id == pid).values(**fields))

    def list_parsers(self, status: str | None = None) -> list[dict]:
        stmt = select(parsers)
        if status:
            stmt = stmt.where(parsers.c.status == status)
        with self._begin() as c:
            return [_row(r) for r in c.execute(stmt.order_by(parsers.c.parser_id))]

    def get_parser(self, parser_id: str) -> dict | None:
        with self._begin() as c:
            return _row(c.execute(select(parsers).where(parsers.c.parser_id == parser_id)).first())

    # ------------------------------------------------------------- fingerprints
    def upsert_fingerprint(self, *, shape_hash: str, family_hash: str, shape: str, example_raw_event_id: str | None = None, routed_parser_id: str | None = None, inc: int = 1) -> None:
        now = _now()
        with self._begin() as c:
            r = c.execute(select(fingerprints.c.count).where(fingerprints.c.shape_hash == shape_hash)).first()
            if r is None:
                c.execute(fingerprints.insert().values(shape_hash=shape_hash, family_hash=family_hash, shape=shape[:4000], example_raw_event_id=example_raw_event_id, first_seen=now, last_seen=now, count=inc, routed_parser_id=routed_parser_id))
            else:
                vals: dict[str, Any] = dict(last_seen=now, count=(r[0] or 0) + inc)
                if routed_parser_id:
                    vals["routed_parser_id"] = routed_parser_id
                c.execute(update(fingerprints).where(fingerprints.c.shape_hash == shape_hash).values(**vals))

    def list_fingerprints(self, limit: int = 500) -> list[dict]:
        with self._begin() as c:
            return [_row(r) for r in c.execute(select(fingerprints).order_by(fingerprints.c.count.desc()).limit(limit))]

    # ------------------------------------------------------------- unknown
    def upsert_unknown_cluster(self, **fields: Any) -> None:
        cid = fields["cluster_id"]
        with self._begin() as c:
            r = c.execute(select(unknown_clusters.c.cluster_id).where(unknown_clusters.c.cluster_id == cid)).first()
            if r is None:
                c.execute(unknown_clusters.insert().values(**fields))
            else:
                c.execute(update(unknown_clusters).where(unknown_clusters.c.cluster_id == cid).values(**fields))

    def find_unknown_cluster(self, tenant_id: str, source_id: str, family_hash: str) -> dict | None:
        with self._begin() as c:
            return _row(c.execute(select(unknown_clusters).where(unknown_clusters.c.tenant_id == tenant_id, unknown_clusters.c.source_id == source_id, unknown_clusters.c.family_hash == family_hash)).first())

    def list_unknown(self, tenant_id: str, status: str | None = None) -> list[dict]:
        stmt = select(unknown_clusters).where(unknown_clusters.c.tenant_id == tenant_id)
        if status:
            stmt = stmt.where(unknown_clusters.c.status == status)
        with self._begin() as c:
            return [_row(r) for r in c.execute(stmt.order_by(unknown_clusters.c.event_count.desc()))]

    def get_unknown(self, cluster_id: str) -> dict | None:
        with self._begin() as c:
            return _row(c.execute(select(unknown_clusters).where(unknown_clusters.c.cluster_id == cluster_id)).first())

    def add_suggestion(self, **fields: Any) -> None:
        fields.setdefault("suggestion_id", ulid())
        fields.setdefault("created_at", _now())
        fields.setdefault("status", "pending")
        with self._begin() as c:
            c.execute(parser_suggestions.insert().values(**fields))

    def list_suggestions(self, status: str | None = None) -> list[dict]:
        stmt = select(parser_suggestions)
        if status:
            stmt = stmt.where(parser_suggestions.c.status == status)
        with self._begin() as c:
            return [_row(r) for r in c.execute(stmt.order_by(parser_suggestions.c.created_at.desc()))]

    def get_suggestion(self, suggestion_id: str) -> dict | None:
        with self._begin() as c:
            return _row(c.execute(select(parser_suggestions).where(parser_suggestions.c.suggestion_id == suggestion_id)).first())

    def update_suggestion(self, suggestion_id: str, **fields: Any) -> None:
        with self._begin() as c:
            c.execute(update(parser_suggestions).where(parser_suggestions.c.suggestion_id == suggestion_id).values(**fields))

    # ------------------------------------------------------------- drift
    def write_drift(self, ev: DriftEvent) -> None:
        with self._begin() as c:
            c.execute(drift_events.insert().values(**ev.model_dump(mode="python")))

    def list_drift(self, tenant_id: str, since: datetime | None = None, limit: int = 200) -> list[dict]:
        stmt = select(drift_events).where(drift_events.c.tenant_id == tenant_id)
        if since:
            stmt = stmt.where(drift_events.c.detected_at >= since)
        with self._begin() as c:
            return [_row(r) for r in c.execute(stmt.order_by(drift_events.c.detected_at.desc()).limit(limit))]

    def write_parser_drift(self, ev: ParserDriftEvent) -> None:
        with self._begin() as c:
            c.execute(parser_drift_events.insert().values(**ev.model_dump(mode="python")))

    def list_parser_drift(self, tenant_id: str, limit: int = 200) -> list[dict]:
        with self._begin() as c:
            return [_row(r) for r in c.execute(select(parser_drift_events).where(parser_drift_events.c.tenant_id == tenant_id).order_by(parser_drift_events.c.detected_at.desc()).limit(limit))]

    def update_parser_drift(self, pdrift_id: str, **fields: Any) -> None:
        with self._begin() as c:
            c.execute(update(parser_drift_events).where(parser_drift_events.c.pdrift_id == pdrift_id).values(**fields))

    # ------------------------------------------------------------- audit / health
    def audit(self, tenant_id: str, actor: str, action: str, target: str, details: dict | None = None) -> None:
        with self._begin() as c:
            c.execute(audit.insert().values(id=ulid(), tenant_id=tenant_id, actor=actor, action=action, target=target, at=_now(), details=details or {}))

    def list_audit(self, tenant_id: str, limit: int = 100) -> list[dict]:
        with self._begin() as c:
            return [_row(r) for r in c.execute(select(audit).where(audit.c.tenant_id == tenant_id).order_by(audit.c.at.desc()).limit(limit))]

    def health(self) -> dict:
        try:
            with self._begin() as c:
                c.execute(text("SELECT 1"))
            return {"ok": True, "backend": "sqlite" if self.is_sqlite else self.engine.dialect.name}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)[:200]}

    def stats(self, tenant_id: str) -> dict:
        with self._begin() as c:
            total = c.execute(select(func.count()).select_from(events).where(events.c.tenant_id == tenant_id)).scalar() or 0
            by_tier = {int(r[0]): int(r[1]) for r in c.execute(select(events.c.tier, func.count()).where(events.c.tenant_id == tenant_id).group_by(events.c.tier))}
            unknown = c.execute(select(func.count()).select_from(unknown_clusters).where(unknown_clusters.c.tenant_id == tenant_id, unknown_clusters.c.status == "open")).scalar() or 0
            n_sources = c.execute(select(func.count()).select_from(sources).where(sources.c.tenant_id == tenant_id)).scalar() or 0
        return {"events": int(total), "by_tier": by_tier, "open_unknown_clusters": int(unknown), "sources": int(n_sources)}


def _fts_escape(q: str) -> str:
    # wrap each term in quotes to neutralise FTS5 operators from untrusted input
    return " ".join('"' + t.replace('"', '""') + '"' for t in q.split()[:16])
