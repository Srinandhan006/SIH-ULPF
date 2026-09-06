"""raw -> ingestion -> parser -> normalization -> storage -> retrieval, on real sample datasets."""
from pathlib import Path

import pytest

from uli.models import RawEnvelope

SAMPLES = Path("logs/samples")

REAL_SAMPLES = [
    ("linux", "linux/linux.log"),
    ("apache", "apache/apache.log"),
    ("ssh", "ssh/ssh.log"),
    ("healthapp", "healthapp/healthapp.log"),
    ("squid", "squid/squid.log"),
    ("zeek_conn", "zeek_conn/zeek_conn.log"),
    ("auth", "auth/auth.log"),
]


@pytest.mark.parametrize("name,relpath", REAL_SAMPLES)
def test_real_dataset_ingests_without_error_and_is_traceable(stack, name, relpath):
    path = SAMPLES / relpath
    if not path.exists():
        pytest.skip(f"sample not fetched: {path} (run scripts/fetch_logs.py)")
    lines = [l for l in path.read_text(encoding="utf-8", errors="replace").splitlines() if l.strip()][:200]
    assert lines, f"no lines in {path}"
    tiers = []
    for line in lines:
        env = RawEnvelope.from_bytes(line.encode(), tenant_id="default", source_id=f"test:{name}", transport="file")
        ev = stack.pipeline.process(env)
        tiers.append(ev.provenance.tier)
        # provenance round-trip: raw is retrievable and hash-verified
        loc, meta = stack.storage.get_raw_location("default", ev.provenance.raw_event_id)
        raw_bytes = stack.raw_store.read(loc)
        assert stack.raw_store.verify(loc, ev.provenance.raw_event_id)
        assert raw_bytes.decode() == line
    # no dataset should be 100% quarantine (tier 7) -- structural/inference should catch most
    quarantine_rate = sum(1 for t in tiers if t == 7) / len(tiers)
    assert quarantine_rate < 0.5, f"{name}: quarantine rate too high ({quarantine_rate:.0%})"


def test_event_id_lookup_round_trip(stack):
    env = RawEnvelope.from_bytes(b"hello world plain text log", tenant_id="default", source_id="rt:1", transport="http")
    ev = stack.pipeline.process(env)
    fetched = stack.storage.get_event("default", ev.event_id)
    assert fetched is not None
    assert fetched["event_id"] == ev.event_id
    assert fetched["provenance"]["raw_event_id"] == ev.provenance.raw_event_id


def test_duplicate_raw_content_is_deduped_in_raw_store_but_both_events_exist(stack):
    line = b"exact duplicate content 12345"
    e1 = stack.pipeline.process(RawEnvelope.from_bytes(line, tenant_id="default", source_id="dup:1", transport="http"))
    e2 = stack.pipeline.process(RawEnvelope.from_bytes(line, tenant_id="default", source_id="dup:1", transport="http"))
    assert e1.provenance.raw_event_id == e2.provenance.raw_event_id
    assert e1.event_id != e2.event_id
    assert e2.provenance.duplicate_of == e2.provenance.raw_event_id


def test_source_registry_tracks_event_counts(stack):
    for i in range(5):
        stack.pipeline.process(RawEnvelope.from_bytes(f"line {i}".encode(), tenant_id="default", source_id="counted:1", transport="http"))
    sources = {s["source_id"]: s for s in stack.storage.list_sources("default")}
    assert sources["counted:1"]["event_count"] == 5
