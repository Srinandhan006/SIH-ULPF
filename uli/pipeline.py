"""The full pipeline for one RawEnvelope: raw store -> fingerprint+parse ladder -> drift observe ->
unknown record -> normalize -> storage write. This function is the P1 "total function" boundary:
callers (worker, API sync-ingest) never see an exception from here."""
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
from uli.models import NormalizedEvent, RawEnvelope, RawRecord, sha256_hex
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

        text = payload.decode("utf-8", errors="replace")
        try:
            ctx, ir = self.engine.run(tenant_id=env.tenant_id, source_id=env.source_id, transport=env.transport, text=text, raw=payload, hints=env.hints)
        except Exception as e:  # noqa: BLE001 — engine is expected to be total, but double-guard here
            log.error("engine_raised", error=str(e)[:300], source_id=env.source_id)
            from uli.fingerprint import fingerprint
            from uli.models import IR, ParseContext

            fp = fingerprint(text)
            ctx = ParseContext(raw=payload, text=text, fingerprint=fp, tenant_id=env.tenant_id, source_id=env.source_id, transport=env.transport, hints=env.hints)
            ir = IR(message=text, tier=7, confidence=0.0, parser_id="engine.error", parser_version="0")
            ir.errors.append(f"engine_exception:{type(e).__name__}")

        drift_event = None
        try:
            drift_event = self.drift.observe(env.tenant_id, env.source_id, ir.parser_id, ctx.fingerprint.shape_hash, ir.confidence)
        except Exception as e:  # noqa: BLE001
            log.warning("drift_observe_failed", error=str(e)[:200])
        if drift_event is not None:
            try:
                drift_event.explained_by = correlate(self.storage, drift_event, self.settings.correlation_window_s)
                self.storage.write_parser_drift(drift_event)  # already written by monitor; update with correlation
                if drift_event.explained_by:
                    self.storage.update_parser_drift(drift_event.pdrift_id, explained_by=drift_event.explained_by)
            except Exception as e:  # noqa: BLE001
                log.warning("correlation_failed", error=str(e)[:200])

        if ir.tier >= 4 and ir.confidence < self.settings.tau_known:
            try:
                record_unknown(self.storage, env.tenant_id, env.source_id, ctx, ir, raw_id, self.settings.unknown_suggest_min_events)
                UNKNOWN_EVENTS.labels(tenant=env.tenant_id).inc()
            except Exception as e:  # noqa: BLE001
                log.warning("unknown_record_failed", error=str(e)[:200])

        ev = normalize(
            ir, tenant_id=env.tenant_id, project_id=env.project_id, source_id=env.source_id, raw_event_id=raw_id,
            raw_loc=loc, received_at=env.received_at, transport=env.transport, peer=env.peer,
            shape_hash=ctx.fingerprint.shape_hash, family_hash=ctx.fingerprint.family_hash,
            embed_raw=self.settings.embed_raw, raw_bytes_for_embed=payload if self.settings.embed_raw else None,
            duplicate_of=duplicate_of,
        )
        self.storage.write_event(ev, shape_hash=ctx.fingerprint.shape_hash, family_hash=ctx.fingerprint.family_hash)
        self.storage.upsert_source(env.tenant_id, env.source_id, transport=env.transport, active_parser_id=ir.parser_id, _inc=1)
        if ir.errors:
            EVENTS_FAILED.labels(tenant=env.tenant_id, error=ir.errors[0][:40]).inc()
        EVENTS_PARSED.labels(tenant=env.tenant_id, tier=str(ir.tier)).inc()
        return ev

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
