"""Real, measured throughput/latency of the single-process pipeline (raw store -> ladder ->
normalize -> SQLite write). Numbers here are read by a human and copied verbatim into
docs/scalability.md -- this file must never be edited to make a number look better; the fix for
a bad number is a real code change, re-run, and a doc update citing the new run.

Progressive load per the project brief: 1K/10K/100K in-process (this machine: {cpu} logical CPUs,
Python 3.12, SQLite backend, single thread, no batching). 1M/10M are documented as NOT run here --
a single unbatched Python process would take on the order of an hour+ at this measured rate, which
is outside this benchmark's time budget; docs/scalability.md states this explicitly rather than
extrapolating a number and presenting it as measured.
"""
from __future__ import annotations

import gc
import json
import os
import statistics
import time
from pathlib import Path

import psutil
import pytest

from uli.models import RawEnvelope

pytestmark = pytest.mark.performance

RESULTS_PATH = Path(__file__).parent / "last_run_results.json"

CORPUS = [
    "Jun 14 15:16:01 web1 sshd[1234]: Failed password for root from 1.2.3.4 port 4444 ssh2",
    '{"level":"info","msg":"request completed","host":"h1","status":200,"latency_ms":12}',
    "192.168.1.5 - - [10/Oct/2025:13:55:36 +0000] \"GET /index.html HTTP/1.1\" 200 2326",
    "<134>1 2025-06-14T15:16:01Z host1 app 1234 - - user=alice action=login result=ok",
    "WIDGETCORP-BOX seq 1 status OK node alpha reading 100 at 1757066400",
    "CEF:0|Vendor|Product|1.0|100|Login Success|3|src=10.0.0.1 dst=10.0.0.2 spt=1234",
]


def _percentile(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    k = (len(sorted_vals) - 1) * p
    f, c = int(k), min(int(k) + 1, len(sorted_vals) - 1)
    if f == c:
        return sorted_vals[f]
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


def _run_load(stack, n: int, source_id: str) -> dict:
    proc = psutil.Process(os.getpid())
    gc.collect()
    rss_before_mb = proc.memory_info().rss / (1024 * 1024)
    cpu_times_before = proc.cpu_times()

    latencies_ms: list[float] = []
    t0 = time.perf_counter()
    for i in range(n):
        line = CORPUS[i % len(CORPUS)]
        env = RawEnvelope.from_bytes(f"{line} seq={i}".encode(), tenant_id="default", source_id=source_id, transport="http")
        e0 = time.perf_counter()
        stack.pipeline.process(env)
        latencies_ms.append((time.perf_counter() - e0) * 1000.0)
    wall_s = time.perf_counter() - t0

    cpu_times_after = proc.cpu_times()
    rss_after_mb = proc.memory_info().rss / (1024 * 1024)
    latencies_ms.sort()
    return {
        "n": n,
        "wall_seconds": round(wall_s, 4),
        "events_per_sec": round(n / wall_s, 1),
        "latency_ms_p50": round(_percentile(latencies_ms, 0.50), 4),
        "latency_ms_p95": round(_percentile(latencies_ms, 0.95), 4),
        "latency_ms_p99": round(_percentile(latencies_ms, 0.99), 4),
        "latency_ms_max": round(latencies_ms[-1], 4),
        "cpu_user_seconds": round(cpu_times_after.user - cpu_times_before.user, 3),
        "cpu_system_seconds": round(cpu_times_after.system - cpu_times_before.system, 3),
        "rss_mb_before": round(rss_before_mb, 1),
        "rss_mb_after": round(rss_after_mb, 1),
        "rss_mb_delta": round(rss_after_mb - rss_before_mb, 1),
    }


@pytest.mark.timeout(900)
@pytest.mark.parametrize("n", [1_000, 10_000, 100_000])
def test_pipeline_throughput_at_scale(stack, n, request):
    result = _run_load(stack, n, source_id=f"perf:{n}")
    result["cpu_count_logical"] = os.cpu_count()
    print(f"\n[perf] n={n} events/sec={result['events_per_sec']} "
          f"p50={result['latency_ms_p50']}ms p95={result['latency_ms_p95']}ms p99={result['latency_ms_p99']}ms "
          f"rss_delta={result['rss_mb_delta']}MB cpu_user={result['cpu_user_seconds']}s")

    existing = {}
    if RESULTS_PATH.exists():
        existing = json.loads(RESULTS_PATH.read_text())
    existing[str(n)] = result
    RESULTS_PATH.write_text(json.dumps(existing, indent=2, sort_keys=True))

    assert result["events_per_sec"] > 0
    assert result["latency_ms_p99"] < 5000
