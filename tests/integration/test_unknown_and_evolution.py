"""Unknown-vendor workflow (tier 7 -> cluster -> suggestion -> promote) and parser-drift ->
evolution -> shadow, exercised through the real pipeline (not just unit-level algorithm tests)."""
from uli.models import RawEnvelope


def test_unknown_vendor_becomes_a_cluster_and_a_promotable_suggestion(stack):
    # Deliberately not JSON/CEF/LEEF/syslog/CLF/KV so tiers 1-2 miss it and it lands in
    # tiers 4-7 (Drain3 / similarity / quarantine) -- the actual "unknown vendor" path.
    lines = [
        "WIDGETCORP-BOX seq 1 status OK node alpha reading 100 at 1757066400",
        "WIDGETCORP-BOX seq 2 status OK node beta reading 142 at 1757066401",
        "WIDGETCORP-BOX seq 3 status FAIL node gamma reading 88 at 1757066402",
        "WIDGETCORP-BOX seq 4 status OK node delta reading 13 at 1757066403",
    ]
    for line in lines:
        stack.pipeline.process(RawEnvelope.from_bytes(line.encode(), tenant_id="default", source_id="widgetcorp:1", transport="http"))
    clusters = stack.storage.list_unknown("default")
    assert clusters, "expected an unknown cluster to be recorded for a never-configured format"
    cluster = clusters[0]
    assert cluster["event_count"] == len(lines)
    assert cluster["templates"], "Drain3 should have mined at least one template"

    from uli.api.app import create_app
    from fastapi.testclient import TestClient

    app = create_app(stack)
    client = TestClient(app)
    r = client.post(f"/v1/unknown/{cluster['cluster_id']}/suggest")
    assert r.status_code == 200, r.text
    suggestion_body = r.json()
    assert suggestion_body["yaml"]

    suggestions = stack.storage.list_suggestions()
    assert suggestions
    sid = suggestions[0]["suggestion_id"]
    r2 = client.post(f"/v1/parsers/suggestions/{sid}/promote", json={"approved_by": "test-admin"})
    assert r2.status_code == 200, r2.text
    assert r2.json()["status"] == "active"

    parsers = {p["parser_id"] for p in stack.storage.list_parsers("active")}
    assert suggestion_body["parser_id"] in parsers


def test_parser_never_raises_even_when_pack_is_malformed_after_load(stack):
    """A pack that references a nonexistent structural pre-parser or has odd extraction should
    degrade to errors on the IR, never crash ingestion (P1)."""
    bad_pack = """
parser_id: vendor.broken.test
version: 1.0.0
signatures:
  - kind: prefix
    startswith: "BROKENTEST"
    required: true
extract:
  - kind: regex
    field: message
    pattern: '(?P<a>\\d+)-(?P<b>\\d+)'
map:
  fields:
    src_endpoint.ip: {from: a, type: int}
"""
    stack.engine.register_pack(bad_pack)
    env = RawEnvelope.from_bytes(b"BROKENTEST not-numbers-at-all", tenant_id="default", source_id="broken:1", transport="http")
    ev = stack.pipeline.process(env)  # must not raise
    assert ev is not None
