"""The full pipeline for one RawEnvelope: raw store -> fingerprint+parse ladder -> drift observe ->
unknown record -> normalize -> storage write. This function is the P1 "total function" boundary:
callers (worker, API sync-ingest) never see an exception from here.

`process()` is the original single-event path (used by tests and low-volume sync ingest).
`process_batch()` is the scalability path (docs/scalability.md §2/§6): it defers the two SQL
writes that fire on *every* event — `write_event` and `upsert_source` — to one bulk statement each
per batch, instead of one round-trip per event per call. Both paths run the identical parsing/
drift/unknown logic via the shared `_compute` helper; only the I/O commit boundary differs."""
from __future__ import annotations

import time
from datetime import datetime, timezone

from uli.config import Settings
from uli.detection.unknown import record_unknown
from uli.drift.correlator import correlate
from uli.drift.parser_drift import DriftMonitor
from uli.engine import ParserEngine
from uli.logging import get_logger
from uli.metrics import EVENTS_FAILED, EVENTS_INGESTED, EVENTS_PARSED, PROCESSING_LATENCY, UNKNOWN_EVENTS
from uli.models import NormalizedEvent, RawEnvelope, RawLocation, RawRecord, sha256_hex
from uli.normalization.ocsf import normalize

log = get_logger("pipeline")


class Pipeline:
    def __init__(self, settings: Settings, storage, raw_store, engine: ParserEngine, drift_monitor: DriftMonitor):
        self.settings = settings
        self.storage = storage
        self.raw_store = raw_store
        self.engine = engine
        self.drift = drift_monitor

    def process(self, env: RawEnvelope) -> NormalizedEvent:
        """Never raises. Any internal failure degrades to a low-confidence quarantined event that
        still carries the raw payload's provenance, satisfying 'fail soft, not silently'."""
        t0 = time.monotonic()
        EVENTS_INGESTED.labels(tenant=env.tenant_id, transport=env.transport).inc()
        try:
            return self._process_inner(env)
        except Exception as e:  # noqa: BLE001 — absolute last resort; should be unreachable
            log.error("pipeline_hard_failure", error=str(e)[:300], source_id=env.source_id)
            EVENTS_FAILED.labels(tenant=env.tenant_id, error=type(e).__name__).inc()
            return self._emergency_event(env, str(e))
        finally:
            PROCESSING_LATENCY.observe(time.monotonic() - t0)

    def _process_inner(self, env: RawEnvelope) -> NormalizedEvent:
        payload = env.payload()
        raw_id = sha256_hex(payload)
        rec = RawRecord(raw_event_id=raw_id, tenant_id=env.tenant_id, source_id=env.source_id, transport=env.transport, peer=env.peer, received_at=env.received_at, payload=payload)
        existing = self.storage.get_raw_location(env.tenant_id, raw_id)
        duplicate_of = None
        if existing is None:
            loc = self.raw_store.append(rec)
            self.storage.index_raw(rec, loc)
        else:
            loc, _ = existing
            duplicate_of = raw_id  # content-identical; normalized layer still gets a fresh event_id

        ev, shape_hash, family_hash = self._compute(env, rec, loc, duplicate_of)
        self.storage.write_event(ev, shape_hash=shape_hash, family_hash=family_hash)
        self.storage.upsert_source(env.tenant_id, env.source_id, transport=env.transport, active_parser_id=ev.provenance.parser_id, _inc=1)
        return ev

    def _compute(self, env: RawEnvelope, rec: RawRecord, loc: RawLocation, duplicate_of: str | None) -> tuple[NormalizedEvent, str, str]:
        """Everything between 'raw bytes are located' and 'normalized event is ready to write':
        parse ladder, drift observation + correlation, unknown-cluster recording, OCSF normalize.
        No SQL writes for the event/source rows themselves — callers own that (single vs. bulk)."""
        text = rec.payload.decode("utf-8", errors="replace")
        try:
            ctx, ir = self.engine.run(tenant_id=env.tenant_id, source_id=env.source_id, transport=env.transport, text=text, raw=rec.payload, hints=env.hints)
        except Exception as e:  # noqa: BLE001 — engine is expected to be total, but double-guard here
            log.error("engine_raised", error=str(e)[:300], source_id=env.source_id)
            from uli.fingerprint import fingerprint
            from uli.models import IR, ParseContext

            fp = fingerprint(text)
            ctx = ParseContext(raw=rec.payload, text=text, fingerprint=fp, tenant_id=env.tenant_id, source_id=env.source_id, transport=env.transport, hints=env.hints)
            ir = IR(message=text, tier=7, confidence=0.0, parser_id="engine.error", parser_version="0")
            ir.errors.append(f"engine_exception:{type(e).__name__}")

        drift_event = None
        try:
            drift_event = self.drift.observe(env.tenant_id, env.source_id, ir.parser_id, ctx.fingerprint.shape_hash, ir.confidence)
        except Exception as e:  # noqa: BLE001
            log.warning("drift_observe_failed", error=str(e)[:200])
        if drift_event is not None:
            # NOTE: DriftMonitor.observe() already persisted drift_event via write_parser_drift;
            # this only ever needs to *update* the row with a correlation result, never re-insert
            # it (a second write_parser_drift() call here used to hit the pdrift_id primary key
            # and raise IntegrityError on every single correlation, silently swallowed by the
            # except below -- meaning `explained_by` was never actually populated in practice, the
            # exact reason H2 stayed "hypothesis, not proven" instead of measured).
            try:
                drift_event.explained_by = correlate(self.storage, drift_event, self.settings.correlation_window_s)
                if drift_event.explained_by:
                    self.storage.update_parser_drift(drift_event.pdrift_id, explained_by=drift_event.explained_by)
            except Exception as e:  # noqa: BLE001
                log.warning("correlation_failed", error=str(e)[:200])

        if ir.tier >= 4 and ir.confidence < self.settings.tau_known:
            try:
                record_unknown(self.storage, env.tenant_id, env.source_id, ctx, ir, rec.raw_event_id, self.settings.unknown_suggest_min_events)
                UNKNOWN_EVENTS.labels(tenant=env.tenant_id).inc()
            except Exception as e:  # noqa: BLE001
                log.warning("unknown_record_failed", error=str(e)[:200])

        ev = normalize(
            ir, tenant_id=env.tenant_id, project_id=env.project_id, source_id=env.source_id, raw_event_id=rec.raw_event_id,
            raw_loc=loc, received_at=env.received_at, transport=env.transport, peer=env.peer,
            shape_hash=ctx.fingerprint.shape_hash, family_hash=ctx.fingerprint.family_hash,
            embed_raw=self.settings.embed_raw, raw_bytes_for_embed=rec.payload if self.settings.embed_raw else None,
            duplicate_of=duplicate_of,
        )
        if ir.errors:
            EVENTS_FAILED.labels(tenant=env.tenant_id, error=ir.errors[0][:40]).inc()
        EVENTS_PARSED.labels(tenant=env.tenant_id, tier=str(ir.tier)).inc()
        return ev, ctx.fingerprint.shape_hash, ctx.fingerprint.family_hash

    # ------------------------------------------------------------- batch (scalability path)
    def process_batch(self, envs: list[RawEnvelope]) -> list[NormalizedEvent]:
        """Same total-function guarantee as process(): a failure on one envelope degrades that one
        event to an emergency record, never aborts the batch or raises to the caller. The SQL
        savings come from collapsing the raw-dedup check, the event insert, and the source upsert
        each from N round-trips to O(1) per batch (docs/scalability.md §2)."""
        if not envs:
            return []

        # Phase 1: batched raw-dedup + index, grouped by tenant (usually one group).
        payloads = [e.payload() for e in envs]
        raw_ids = [sha256_hex(p) for p in payloads]
        recs = [RawRecord(raw_event_id=raw_ids[i], tenant_id=envs[i].tenant_id, source_id=envs[i].source_id,
                           transport=envs[i].transport, peer=envs[i].peer, received_at=envs[i].received_at, payload=payloads[i])
                for i in range(len(envs))]

        by_tenant: dict[str, list[int]] = {}
        for i, env in enumerate(envs):
            by_tenant.setdefault(env.tenant_id, []).append(i)

        existing: dict[tuple[str, str], RawLocation] = {}
        for tenant_id, idxs in by_tenant.items():
            try:
                found = self.storage.find_raw_locations(tenant_id, [raw_ids[i] for i in idxs])
            except Exception as e:  # noqa: BLE001 — dedup lookup failure must degrade, not abort the batch
                log.warning("find_raw_locations_failed", error=str(e)[:200])
                found = {}
            for rid, d in found.items():
                existing[(tenant_id, rid)] = RawLocation(segment=d["segment"], offset=d["offset"], length=d["length"])

        locs: list[RawLocation] = [None] * len(envs)  # type: ignore[list-item]
        dup_of: list[str | None] = [None] * len(envs)
        new_index_rows: list[tuple[RawRecord, RawLocation]] = []
        for i, env in enumerate(envs):
            key = (env.tenant_id, raw_ids[i])
            if key in existing:
                locs[i] = existing[key]
                dup_of[i] = raw_ids[i]
                continue
            try:
                loc = self.raw_store.append(recs[i])
            except Exception as e:  # noqa: BLE001 — mirrors _emergency_event's raw-store fallback
                log.error("raw_store_append_failed", error=str(e)[:200])
                loc = RawLocation(segment="unrecoverable", offset=0, length=len(payloads[i]))
            locs[i] = loc
            new_index_rows.append((recs[i], loc))
            existing[key] = loc  # a duplicate arriving twice within the same batch resolves to the first
        if new_index_rows:
            try:
                self.storage.index_raw_bulk(new_index_rows)
            except Exception as e:  # noqa: BLE001
                log.warning("index_raw_bulk_failed", error=str(e)[:200])

        # Phase 2: parse ladder / drift / unknown / normalize per event (CPU-bound, no per-event SQL writes).
        results: list[NormalizedEvent] = []
        event_rows: list[tuple[NormalizedEvent, str, str]] = []
        source_incs: dict[str, dict[str, dict]] = {}  # tenant_id -> source_id -> agg
        for i, env in enumerate(envs):
            t0 = time.monotonic()
            EVENTS_INGESTED.labels(tenant=env.tenant_id, transport=env.transport).inc()
            try:
                ev, shape_hash, family_hash = self._compute(env, recs[i], locs[i], dup_of[i])
                results.append(ev)
                event_rows.append((ev, shape_hash, family_hash))
                agg = source_incs.setdefault(env.tenant_id, {}).setdefault(env.source_id, {"inc": 0, "transport": env.transport, "active_parser_id": ev.provenance.parser_id})
                agg["inc"] += 1
                agg["transport"] = env.transport
                agg["active_parser_id"] = ev.provenance.parser_id
            except Exception as e:  # noqa: BLE001 — absolute last resort for this one event
                log.error("pipeline_hard_failure", error=str(e)[:300], source_id=env.source_id)
                EVENTS_FAILED.labels(tenant=env.tenant_id, error=type(e).__name__).inc()
                results.append(self._emergency_event(env, str(e)))
            finally:
                PROCESSING_LATENCY.observe(time.monotonic() - t0)

        if event_rows:
            try:
                self.storage.write_events_bulk(event_rows)
            except Exception as e:  # noqa: BLE001
                log.error("write_events_bulk_failed", error=str(e)[:300])
                for ev, shape_hash, family_hash in event_rows:  # fall back to per-event writes rather than lose data
                    try:
                        self.storage.write_event(ev, shape_hash=shape_hash, family_hash=family_hash)
                    except Exception as e2:  # noqa: BLE001
                        log.error("write_event_fallback_failed", error=str(e2)[:200], event_id=ev.event_id)
        for tenant_id, aggs in source_incs.items():
            try:
                self.storage.upsert_sources_bulk(tenant_id, aggs)
            except Exception as e:  # noqa: BLE001
                log.warning("upsert_sources_bulk_failed", error=str(e)[:200])
        return results

    def _emergency_event(self, env: RawEnvelope, error: str) -> NormalizedEvent:
        """Absolute fallback: even if everything above fails, we still store *something* traceable."""
        from uli.models import IR, ParseContext, sha256_hex
        from uli.fingerprint import fingerprint

        payload = env.payload()
        raw_id = sha256_hex(payload)
        try:
            rec = RawRecord(raw_event_id=raw_id, tenant_id=env.tenant_id, source_id=env.source_id, transport=env.transport, peer=env.peer, received_at=env.received_at, payload=payload)
            loc = self.raw_store.append(rec)
            self.storage.index_raw(rec, loc)
        except Exception:
            from uli.models import RawLocation

            loc = RawLocation(segment="unrecoverable", offset=0, length=len(payload))
        text = payload.decode("utf-8", errors="replace")
        fp = fingerprint(text[:200]) if text else fingerprint("")
        ir = IR(message=text[:2000], tier=7, confidence=0.0, parser_id="engine.emergency", parser_version="0", errors=[f"pipeline_failure:{error[:200]}"])
        ev = normalize(ir, tenant_id=env.tenant_id, project_id=env.project_id, source_id=env.source_id, raw_event_id=raw_id, raw_loc=loc,
                       received_at=env.received_at, transport=env.transport, peer=env.peer, shape_hash=fp.shape_hash, family_hash=fp.family_hash, embed_raw=False)
        try:
            self.storage.write_event(ev, shape_hash=fp.shape_hash, family_hash=fp.family_hash)
        except Exception:
            log.error("emergency_event_could_not_be_stored", raw_event_id=raw_id)
        return ev
