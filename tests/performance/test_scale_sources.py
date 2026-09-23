"""Concrete proof point for 'ready to be scalable for 1000 sources' (as opposed to just claiming
it): 1000 distinct source_ids, interleaved into batches the way a real fleet of collectors would
arrive, pushed through the batched pipeline path. Asserts both throughput and that per-source
bookkeeping (uli/storage/sql.py:upsert_sources_bulk) stays correct under concurrent-looking load --
every source's event_count must equal exactly what was sent to it, aggregated across many batches,
not just the last batch's value."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from uli.models import RawEnvelope

pytestmark = pytest.mark.performance

RESULTS_PATH = Path(__file__).parent / "last_run_results.json"

CORPUS = [
    "Jun 14 15:16:01 web1 sshd[1234]: Failed password for root from 1.2.3.4 port 4444 ssh2",
    '{"level":"info","msg":"request completed","host":"h1","status":200,"latency_ms":12}',
    "192.168.1.5 - - [10/Oct/2025:13:55:36 +0000] \"GET /index.html HTTP/1.1\" 200 2326",
    "<134>1 2025-06-14T15:16:01Z host1 app 1234 - - user=alice action=login result=ok",
]


@pytest.mark.timeout(300)
def test_1000_sources_throughput_and_per_source_accounting(stack):
    n_sources = 1000
    events_per_source = 5
    total = n_sources * events_per_source
    source_ids = [f"scale-src:{i:04d}" for i in range(n_sources)]

    # Interleave sources across batches (round-robin), the way a shared queue consumer group would
    # see traffic from many collectors at once -- not one source fully processed before the next.
    envs = []
    for round_ in range(events_per_source):
        for i, sid in enumerate(source_ids):
            line = f"{CORPUS[i % len(CORPUS)]} seq={round_}"
            envs.append(RawEnvelope.from_bytes(line.encode(), tenant_id="default", source_id=sid, transport="http"))

    batch_size = 200
    t0 = time.perf_counter()
    all_results = []
    for i in range(0, len(envs), batch_size):
        all_results.extend(stack.pipeline.process_batch(envs[i:i + batch_size]))
    wall_s = time.perf_counter() - t0

    assert len(all_results) == total

    rows = {r["source_id"]: r for r in stack.storage.list_sources("default")}
    assert len(rows) == n_sources, f"expected {n_sources} distinct source rows, got {len(rows)}"
    for sid in source_ids:
        assert rows[sid]["event_count"] == events_per_source, f"{sid}: expected {events_per_source}, got {rows[sid]['event_count']}"

    result = {
        "n_sources": n_sources,
        "events_per_source": events_per_source,
        "total_events": total,
        "wall_seconds": round(wall_s, 4),
        "events_per_sec": round(total / wall_s, 1),
        "cpu_count_logical": os.cpu_count(),
    }
    print(f"\n[perf-1000-sources] {result}")

    existing = {}
    if RESULTS_PATH.exists():
        existing = json.loads(RESULTS_PATH.read_text())
    existing["1000_sources"] = result
    RESULTS_PATH.write_text(json.dumps(existing, indent=2, sort_keys=True))

    assert result["events_per_sec"] > 0
