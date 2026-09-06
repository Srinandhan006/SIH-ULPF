"""Unit tests for the ladder orchestrator itself: total-function guarantee, tier assignment,
routing cache behavior."""
from uli.bootstrap import build_stack


def test_known_vendor_hits_tier1_or_2(tmp_settings):
    st = build_stack(tmp_settings)
    ctx, ir = st.engine.run(tenant_id="t", source_id="s", transport="http", text='{"level":"info","msg":"hi","timestamp":"2026-09-05T10:00:00Z"}', raw=b"")
    assert ir.tier == 1
    assert ir.confidence > 0.6


def test_completely_unknown_format_never_raises_and_falls_back(tmp_settings):
    st = build_stack(tmp_settings)
    weird = "###ACME-BOX### seq~1 code~200 whatever~true unrecognizable_field=xyz123"
    ctx, ir = st.engine.run(tenant_id="t", source_id="new-vendor", transport="http", text=weird, raw=b"")
    assert ir.tier >= 4  # never tier 0, never an exception
    assert ir.confidence >= 0.0


def test_routing_cache_is_used_on_second_identical_shape(tmp_settings):
    st = build_stack(tmp_settings)
    line1 = "Sep  5 10:00:01 host01 sshd[1234]: Accepted publickey for root from 192.168.1.5 port 22 ssh2"
    line2 = "Sep  6 11:02:03 host02 sshd[5678]: Accepted publickey for alice from 10.0.0.9 port 22 ssh2"
    st.engine.run(tenant_id="t", source_id="s", transport="syslog-udp", text=line1, raw=b"")
    assert len(st.engine._routing_cache) >= 1
    ctx2, ir2 = st.engine.run(tenant_id="t", source_id="s", transport="syslog-udp", text=line2, raw=b"")
    assert ir2.parser_id == "structural.syslog3164"


def test_max_line_bytes_truncates_but_does_not_crash(tmp_settings):
    st = build_stack(tmp_settings)
    huge = "A" * (tmp_settings.max_line_bytes * 3)
    ctx, ir = st.engine.run(tenant_id="t", source_id="s", transport="http", text=huge, raw=huge.encode())
    assert ctx.truncated is True


def test_binary_garbage_never_raises(tmp_settings):
    st = build_stack(tmp_settings)
    text = bytes(range(0, 256)).decode("utf-8", errors="replace")
    ctx, ir = st.engine.run(tenant_id="t", source_id="s", transport="http", text=text, raw=text.encode())
    assert ir is not None
