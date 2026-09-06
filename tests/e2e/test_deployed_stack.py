"""End-to-end scenarios against the real `docker compose up` deployment: api + worker(s) + redis,
built images (not the in-process TestClient used by tests/integration). Each source_id is
namespaced with a run-unique suffix since the compose volume persists across runs."""
from __future__ import annotations

import time
import uuid

import pytest

pytestmark = pytest.mark.e2e

RUN = uuid.uuid4().hex[:8]


def test_health_reports_redis_backed_queue(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["db"]["ok"] is True


def test_known_vendor_line_ingests_with_high_confidence(client):
    source = f"e2e-known-{RUN}"
    r = client.post("/v1/ingest", json={"source_id": source, "lines": [
        "Jun 14 15:16:01 web1 sshd[1234]: Failed password for root from 1.2.3.4 port 4444 ssh2",
    ]})
    assert r.status_code == 200, r.text
    event_id = r.json()["event_ids"][0]

    ev = client.get(f"/v1/events/{event_id}").json()
    assert ev["provenance"]["tier"] in (1, 2, 3)
    assert ev["provenance"]["confidence"] >= 0.5


def test_raw_provenance_round_trip_over_http(client):
    source = f"e2e-raw-{RUN}"
    line = "raw provenance round trip through the deployed stack"
    r = client.post("/v1/ingest", json={"source_id": source, "lines": [line]})
    event_id = r.json()["event_ids"][0]
    raw_event_id = client.get(f"/v1/events/{event_id}").json()["provenance"]["raw_event_id"]

    raw = client.get(f"/v1/raw/{raw_event_id}")
    assert raw.status_code == 200, raw.text
    body = raw.json()
    assert body["sha256_verified"] is True
    assert body["payload"] == line


def test_format_drift_does_not_break_ingestion(client):
    """v1 format 'timestamp host process[pid]: message' drifting to
    'timestamp host process(pid): severity message' must keep producing traceable events."""
    source = f"e2e-drift-{RUN}"
    v1 = "Jun 14 15:16:01 host1 myapp[555]: connection accepted from 10.0.0.5"
    v2 = "Jun 14 15:17:02 host1 myapp(555): INFO connection accepted from 10.0.0.6"
    ids = []
    for line in (v1, v2):
        r = client.post("/v1/ingest", json={"source_id": source, "lines": [line]})
        assert r.status_code == 200, r.text
        ids.append(r.json()["event_ids"][0])
    for eid in ids:
        ev = client.get(f"/v1/events/{eid}").json()
        assert ev["provenance"]["tier"] in range(1, 8)


def test_unknown_vendor_produces_a_cluster(client):
    source = f"e2e-widgetcorp-{RUN}"
    for i in range(4):
        line = f"WIDGETCORP-BOX seq {i} status OK node alpha reading {100 + i} at 175706640{i}"
        r = client.post("/v1/ingest", json={"source_id": source, "lines": [line]})
        assert r.status_code == 200, r.text

    deadline = time.monotonic() + 5
    clusters = []
    while time.monotonic() < deadline:
        clusters = [c for c in client.get("/v1/unknown").json() if c["source_id"] == source]
        if clusters:
            break
        time.sleep(0.5)
    assert clusters, "expected a cluster for a never-configured vendor format"
    assert clusters[0]["event_count"] == 4


def test_unknown_cluster_can_be_promoted_to_active_parser(client):
    source = f"e2e-promote-{RUN}"
    for i in range(4):
        line = f"GIZMOTECH-UNIT id {i} state RUNNING zone north load {10 * i} at 175706650{i}"
        client.post("/v1/ingest", json={"source_id": source, "lines": [line]})

    deadline = time.monotonic() + 5
    cluster = None
    while time.monotonic() < deadline:
        matches = [c for c in client.get("/v1/unknown").json() if c["source_id"] == source]
        if matches:
            cluster = matches[0]
            break
        time.sleep(0.5)
    assert cluster is not None

    r = client.post(f"/v1/unknown/{cluster['cluster_id']}/suggest")
    assert r.status_code == 200, r.text
    suggestion = r.json()
    assert suggestion["yaml"]

    suggestions = client.get("/v1/parsers/suggestions").json()
    sid = next(s["suggestion_id"] for s in suggestions if s["parser_id"] == suggestion["parser_id"])
    r2 = client.post(f"/v1/parsers/suggestions/{sid}/promote", json={"approved_by": "e2e-test"})
    assert r2.status_code == 200, r2.text
    assert r2.json()["status"] == "active"

    parsers = {p["parser_id"] for p in client.get("/v1/parsers").json()}
    assert suggestion["parser_id"] in parsers


def test_stats_and_sources_reflect_ingested_events(client):
    source = f"e2e-stats-{RUN}"
    for i in range(3):
        client.post("/v1/ingest", json={"source_id": source, "lines": [f"stats line {i}"]})
    sources = {s["source_id"]: s for s in client.get("/v1/sources").json()}
    assert sources[source]["event_count"] == 3
    assert client.get("/v1/stats").json()["events"] >= 3


def test_metrics_endpoint_is_prometheus_text(client):
    r = client.get("/metrics")
    assert r.status_code == 200
    assert "events_ingested" in r.text or "python_gc_objects_collected_total" in r.text
