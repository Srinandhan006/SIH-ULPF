"""Adversarial and edge-case inputs. Every case's only real assertion is: the pipeline never
raises and always produces a traceable NormalizedEvent (docs/architecture.md P1)."""
import time

import pytest

from uli.models import RawEnvelope

ADVERSARIAL_LINES = [
    b"",  # empty
    b"\x00\x01\x02\x03\xff\xfe",  # binary garbage
    ("a" * 500_000).encode(),  # extremely long line
    b'{"a": ' + b"[" * 10000 + b"]" * 10000 + b"}",  # deeply nested JSON-ish
    b"=" * 10000,  # malformed KV delimiters, degenerate
    b"level=" * 5000 + b"x",  # repeated key pattern
    "日本語のログ 🔥💥 unicode test с русским текстом".encode(),  # unicode / emoji
    b"CEF:0|||||||",  # CEF with empty fields
    b"LEEF:2.0||||",  # LEEF malformed
    b"<999999999999999999999999>1 not-a-real-pri x",  # PRI overflow
    b"\r\n\r\n\r\n",  # only line breaks
    ("field=" + "\\" * 1000 + '"unterminated').encode(),  # unterminated quote
    b"a" * 70000,  # exceeds max_line_bytes -> must truncate, not crash
    "".join(chr(i) for i in range(0x20)).encode("utf-8", "replace"),  # control characters
    b"(" * 1000 + b")" * 1000,  # nested parens (regex catastrophic-backtracking bait)
    b"a" * 40 + b"=" * 40 + b"a" * 40,  # ReDoS-shaped repeated pattern for naive KV regex
]


@pytest.mark.parametrize("payload", ADVERSARIAL_LINES, ids=[f"case{i}" for i in range(len(ADVERSARIAL_LINES))])
def test_adversarial_input_never_crashes_pipeline(stack, payload):
    env = RawEnvelope.from_bytes(payload, tenant_id="default", source_id="adversarial:1", transport="http")
    t0 = time.monotonic()
    ev = stack.pipeline.process(env)  # must not raise
    elapsed = time.monotonic() - t0
    assert ev is not None
    assert ev.provenance.tier in range(1, 8)
    assert 0.0 <= ev.provenance.confidence <= 1.0
    assert elapsed < 2.0, f"adversarial input took {elapsed:.2f}s -- possible ReDoS/hang"


def test_regex_timeout_guard_actually_bounds_worst_case_pack(stack):
    """A pack author writes a catastrophic regex; the timeout guard must still bound runtime."""
    evil_pack = """
parser_id: vendor.evil.redos
version: 1.0.0
signatures:
  - kind: prefix
    startswith: "REDOS"
    required: true
extract:
  - kind: regex
    field: message
    pattern: '^(a+)+$'
map:
  fields: {}
"""
    stack.engine.register_pack(evil_pack)
    payload = ("REDOS " + "a" * 40 + "!").encode()
    env = RawEnvelope.from_bytes(payload, tenant_id="default", source_id="redos:1", transport="http")
    t0 = time.monotonic()
    ev = stack.pipeline.process(env)
    elapsed = time.monotonic() - t0
    assert ev is not None
    assert elapsed < 1.0, f"regex timeout guard failed to bound runtime: {elapsed:.2f}s"


def test_out_of_order_and_missing_timestamps(stack):
    lines = [b"no timestamp at all here", b"2020-01-01T00:00:00Z old event", b"2030-01-01T00:00:00Z future event"]
    for l in lines:
        ev = stack.pipeline.process(RawEnvelope.from_bytes(l, tenant_id="default", source_id="ooo:1", transport="http"))
        assert ev.ocsf["time"] is not None  # falls back to received_at when unparseable


def test_null_and_missing_fields_do_not_crash_normalization(stack):
    ev = stack.pipeline.process(RawEnvelope.from_bytes(b'{"a": null, "b": "", "c": [1,2,null]}', tenant_id="default", source_id="nulls:1", transport="http"))
    assert ev is not None


def test_duplicate_events_burst(stack):
    payload = b"burst duplicate line"
    for _ in range(50):
        ev = stack.pipeline.process(RawEnvelope.from_bytes(payload, tenant_id="default", source_id="burst:1", transport="http"))
    assert ev is not None


def test_queue_backend_memory_survives_many_messages_without_blocking(tmp_settings):
    from uli.ingestion.queue import MemoryQueue

    q = MemoryQueue(maxsize=1000)
    for i in range(500):
        q.publish(RawEnvelope.from_bytes(f"m{i}".encode(), tenant_id="default", source_id="q:1"))
    assert q.depth() == 500
