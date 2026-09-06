"""Wire everything together once per process (API, worker). Kept in one place so tests and
scripts construct the same stack the real services use."""
from __future__ import annotations

from dataclasses import dataclass

from uli.config import Settings, get_settings
from uli.detection.similarity import SimilarityIndex
from uli.drift.parser_drift import DriftMonitor
from uli.drift.resource import ResourceDriftAgent
from uli.engine import ParserEngine
from uli.ingestion.queue import Queue, build_queue
from uli.logging import configure, get_logger
from uli.parsers.templates import TemplateMinerPool
from uli.pipeline import Pipeline
from uli.storage.raw_store import LocalRawStore
from uli.storage.sql import SQLStorage

log = get_logger("bootstrap")


@dataclass
class Stack:
    settings: Settings
    storage: SQLStorage
    raw_store: LocalRawStore
    engine: ParserEngine
    drift_monitor: DriftMonitor
    pipeline: Pipeline
    queue: Queue
    resource_drift: ResourceDriftAgent


def build_stack(settings: Settings | None = None) -> Stack:
    s = settings or get_settings()
    configure(s.log_level, s.log_json)
    storage = SQLStorage(s.effective_db_url)
    raw_store = LocalRawStore(s.raw_dir, s.raw_segment_max_bytes, s.raw_compress_on_rotate)
    templates = TemplateMinerPool(s.drain_dir, s.drain_sim_th, s.drain_depth, s.drain_max_clusters)
    engine = ParserEngine(s, storage, templates)
    drift_monitor = DriftMonitor(storage, s.drift_window_events, s.drift_window_seconds, s.drift_js_threshold, s.drift_consecutive_windows, s.drift_confidence_drop)
    pipeline = Pipeline(s, storage, raw_store, engine, drift_monitor)
    queue = build_queue(s)
    resource_drift = ResourceDriftAgent(s, storage, raw_store)
    log.info("stack_ready", db=s.effective_db_url, queue=queue.name, parsers=len(engine.declarative))
    return Stack(settings=s, storage=storage, raw_store=raw_store, engine=engine, drift_monitor=drift_monitor, pipeline=pipeline, queue=queue, resource_drift=resource_drift)
