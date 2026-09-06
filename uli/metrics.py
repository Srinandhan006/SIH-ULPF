"""Prometheus metrics — observability of the observability system."""
from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram, generate_latest, CONTENT_TYPE_LATEST, REGISTRY

EVENTS_INGESTED = Counter("uli_events_ingested_total", "Raw events accepted", ["tenant", "transport"])
EVENTS_PARSED = Counter("uli_events_parsed_total", "Events that produced a normalized event", ["tenant", "tier"])
EVENTS_FAILED = Counter("uli_events_failed_total", "Events with processing errors (still stored)", ["tenant", "error"])
UNKNOWN_EVENTS = Counter("uli_unknown_events_total", "Events quarantined as unknown", ["tenant"])
PARSER_CONFIDENCE = Histogram("uli_parser_confidence", "Confidence distribution", ["parser_id"], buckets=[0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0])
PARSER_DRIFT = Counter("uli_parser_drift_total", "Parser drift events", ["source_id", "parser_id"])
NORMALIZATION_FAILURES = Counter("uli_normalization_failures_total", "Schema validation failures (soft)", ["tenant"])
PROCESSING_LATENCY = Histogram("uli_processing_latency_seconds", "Per-event pipeline latency", buckets=[0.0005, 0.001, 0.002, 0.005, 0.01, 0.02, 0.05, 0.1, 0.5, 1, 5])
QUEUE_DEPTH = Gauge("uli_queue_depth", "Pending messages in queue")
ML_LATENCY = Histogram("uli_ml_latency_seconds", "ML sidecar call latency", buckets=[0.001, 0.005, 0.01, 0.02, 0.05, 0.1, 0.5])
ML_FAILURES = Counter("uli_ml_failures_total", "ML sidecar failures/timeouts", ["reason"])
STORAGE_LATENCY = Histogram("uli_storage_latency_seconds", "Storage write latency", ["op"], buckets=[0.0005, 0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1])
DRIFT_EVENTS = Counter("uli_drift_events_total", "Resource drift events", ["kind", "severity"])
ROUTING_CACHE = Counter("uli_routing_cache_total", "Routing cache lookups", ["result"])
PARSER_HEALTH = Gauge("uli_parser_health", "1 healthy, 0.5 degraded, 0 unknown", ["source_id", "parser_id"])


def render() -> tuple[bytes, str]:
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST
