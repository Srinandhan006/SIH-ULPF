"""Parser-drift detection (docs/architecture.md §5.1). Statistical formulation: Jensen-Shannon
divergence between a reference shape-histogram (captured at onboarding/promotion) and the current
sliding window, plus a confidence EWMA. This mirrors the well-known prior art described in
docs/research.md §5 (Cisco/Splunk patent, generic data-drift tooling) — we implement, not claim."""
from __future__ import annotations

import math
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from threading import Lock

from uli.ids import ulid
from uli.logging import get_logger
from uli.metrics import PARSER_DRIFT, PARSER_HEALTH
from uli.models import ParserDriftEvent

log = get_logger("drift.parser")


def js_divergence(p: Counter, q: Counter) -> float:
    keys = set(p) | set(q)
    if not keys:
        return 0.0
    pt, qt = sum(p.values()) or 1, sum(q.values()) or 1
    pd = {k: p.get(k, 0) / pt for k in keys}
    qd = {k: q.get(k, 0) / qt for k in keys}
    m = {k: 0.5 * (pd[k] + qd[k]) for k in keys}

    def kl(a: dict, b: dict) -> float:
        s = 0.0
        for k in keys:
            if a[k] > 0 and b[k] > 0:
                s += a[k] * math.log2(a[k] / b[k])
        return s

    return 0.5 * kl(pd, m) + 0.5 * kl(qd, m)


@dataclass
class _Window:
    shapes: Counter = field(default_factory=Counter)
    conf_sum: float = 0.0
    n: int = 0
    started: float = field(default_factory=time.monotonic)


class DriftMonitor:
    def __init__(self, storage, window_events: int, window_seconds: float, js_threshold: float, consecutive: int, confidence_drop: float):
        self.storage = storage
        self.window_events, self.window_seconds = window_events, window_seconds
        self.js_threshold, self.consecutive_required, self.confidence_drop = js_threshold, consecutive, confidence_drop
        self._windows: dict[tuple[str, str], _Window] = defaultdict(_Window)
        self._consecutive_hits: dict[tuple[str, str], int] = defaultdict(int)
        self._ewma_conf: dict[tuple[str, str], float] = {}
        self._reference: dict[tuple[str, str], tuple[Counter, float]] = {}
        self._lock = Lock()

    def set_reference(self, source_id: str, parser_id: str, histogram: dict[str, int], mean_confidence: float) -> None:
        self._reference[(source_id, parser_id)] = (Counter(histogram), mean_confidence)

    def observe(self, tenant_id: str, source_id: str, parser_id: str, shape_hash: str, confidence: float) -> ParserDriftEvent | None:
        if parser_id in ("engine.quarantine", "none"):
            return None
        key = (source_id, parser_id)
        with self._lock:
            w = self._windows[key]
            w.shapes[shape_hash] += 1
            w.conf_sum += confidence
            w.n += 1
            prev = self._ewma_conf.get(key, confidence)
            self._ewma_conf[key] = 0.9 * prev + 0.1 * confidence
            elapsed = time.monotonic() - w.started
            if w.n < self.window_events and elapsed < self.window_seconds:
                return None
            window_hist, window_n, window_mean = Counter(w.shapes), w.n, (w.conf_sum / w.n if w.n else 0.0)
            self._windows[key] = _Window()
        ref = self._reference.get(key)
        if ref is None:
            self.set_reference(source_id, parser_id, dict(window_hist), window_mean)
            return None
        ref_hist, ref_mean = ref
        js = js_divergence(ref_hist, window_hist)
        conf_drop = max(0.0, ref_mean - self._ewma_conf.get(key, window_mean))
        drifted = js > self.js_threshold or conf_drop > self.confidence_drop
        if not drifted:
            self._consecutive_hits[key] = 0
            PARSER_HEALTH.labels(source_id=source_id, parser_id=parser_id).set(1.0)
            return None
        self._consecutive_hits[key] += 1
        PARSER_HEALTH.labels(source_id=source_id, parser_id=parser_id).set(0.5)
        if self._consecutive_hits[key] < self.consecutive_required:
            return None  # rare/transient => anomaly, not sustained drift
        self._consecutive_hits[key] = 0
        PARSER_DRIFT.labels(source_id=source_id, parser_id=parser_id).inc()
        reason = "js_divergence" if js > self.js_threshold else "confidence_drop"
        ev = ParserDriftEvent(
            tenant_id=tenant_id, source_id=source_id, parser_id=parser_id, parser_version="",
            js_divergence=round(js, 4), confidence_before=round(ref_mean, 4), confidence_after=round(self._ewma_conf.get(key, window_mean), 4),
            reference_histogram=dict(ref_hist), current_histogram=dict(window_hist), window_size=window_n, reason=reason,
        )
        if self.storage:
            self.storage.write_parser_drift(ev)
            self.storage.upsert_source(tenant_id, source_id, parser_health="degraded", _inc=0)
        log.warning("parser_drift_detected", source_id=source_id, parser_id=parser_id, js=js, reason=reason)
        return ev
