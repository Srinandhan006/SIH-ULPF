"""Exercise the HTTP surface directly (not just the underlying storage calls) -- this is the
level at which serialization bugs (e.g. datetime not JSON-encodable) actually surface."""


def test_ingest_then_list_and_get_event(api_client):
    r = api_client.post("/v1/ingest", json={"source_id": "api:1", "lines": ["hello api world"]})
    assert r.status_code == 200, r.text
    event_id = r.json()["event_ids"][0]

    r = api_client.get("/v1/events", params={"source_id": "api:1"})
    assert r.status_code == 200
    assert len(r.json()) == 1

    r = api_client.get(f"/v1/events/{event_id}")
    assert r.status_code == 200
    assert r.json()["event_id"] == event_id


def test_get_raw_round_trip_over_http(api_client):
    r = api_client.post("/v1/ingest", json={"source_id": "api:2", "lines": ["raw round trip line"]})
    raw_event_id = api_client.get(f"/v1/events/{r.json()['event_ids'][0]}").json()["provenance"]["raw_event_id"]

    r = api_client.get(f"/v1/raw/{raw_event_id}")
    assert r.status_code == 200, r.text  # regression: meta dict contains datetimes, must be JSON-serializable
    body = r.json()
    assert body["sha256_verified"] is True
    assert body["payload"] == "raw round trip line"


def test_sources_and_stats_over_http(api_client):
    api_client.post("/v1/ingest", json={"source_id": "api:3", "lines": ["a", "b", "c"]})
    r = api_client.get("/v1/sources")
    assert r.status_code == 200
    assert any(s["source_id"] == "api:3" and s["event_count"] == 3 for s in r.json())

    r = api_client.get("/v1/stats")
    assert r.status_code == 200
    assert r.json()["events"] >= 3


def test_health_and_metrics_over_http(api_client):
    assert api_client.get("/health").status_code == 200
    r = api_client.get("/metrics")
    assert r.status_code == 200
    assert "python_gc_objects_collected_total" in r.text
