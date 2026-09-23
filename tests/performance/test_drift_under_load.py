"""docs/roadmap.md previously listed 'drift-evolution-under-load not separately load-tested' as an
open gap: tests/unit/test_drift_detection.py exercises DriftMonitor directly with 5-event windows,
never through the real batched pipeline at a realistic window size. This drives full-size windows
(default drift_window_events=200) through Pipeline.process_batch -- the same code path
uli/workers/runner.py uses in production -- across many batches, and asserts drift still fires
correctly once the stream shifts shape."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from uli.bootstrap import build_stack
from uli.config import Settings, reset_settings_cache
from uli.models import RawEnvelope

pytestmark = pytest.mark.performance

RESULTS_PATH = Path(__file__).parent / "last_run_results.json"

SHAPE_A = [
    '{"level":"info","msg":"request completed","host":"h1","status":200,"latency_ms":12}',
    '{"level":"info","msg":"request completed","host":"h2","status":200,"latency_ms":9}',
]
SHAPE_B = [
    '{"level":"warn","event":"disk_pressure","node":"n1","pct_used":92,"threshold":90,"mount":"/data","fs":"ext4"}',
    '{"level":"warn","event":"disk_pressure","node":"n2","pct_used":95,"threshold":90,"mount":"/data","fs":"ext4"}',
]


@pytest.mark.timeout(300)
def test_drift_detected_correctly_under_batched_load(tmp_path):
    reset_settings_cache()
    settings = Settings(data_dir=tmp_path / "data", parsers_dir=Path("parsers"), schemas_dir=Path("schemas"),
                        queue_backend="memory", ml_enabled=False)  # default-size drift windows (200 events / 60s)
    stack = build_stack(settings)
    try:
        source_id = "load-drift:1"
        window = settings.drift_window_events
        batch_size = 200

        def feed(lines: list[str], n: int) -> None:
            i = 0
            while i < n:
                chunk = min(batch_size, n - i)
                envs = [RawEnvelope.from_bytes(lines[(i + j) % len(lines)].encode(), tenant_id="default", source_id=source_id, transport="http") for j in range(chunk)]
                stack.pipeline.process_batch(envs)
                i += chunk

        t0 = time.perf_counter()
        feed(SHAPE_A, window * 2)  # window 1 sets reference, window 2 confirms no drift
        feed(SHAPE_B, window * 3)  # window 3: 1st consecutive hit; window 4: fires; window 5: margin
        wall_s = time.perf_counter() - t0
        total_events = window * 5

        parser_drifts = stack.storage.list_parser_drift("default")
        assert parser_drifts, f"expected at least one parser-drift event across {total_events} events under load; got none"
        assert parser_drifts[0]["js_divergence"] > settings.drift_js_threshold

        result = {
            "window_events": window,
            "total_events": total_events,
            "wall_seconds": round(wall_s, 4),
            "events_per_sec": round(total_events / wall_s, 1),
            "drift_events_fired": len(parser_drifts),
        }
        print(f"\n[perf-drift-under-load] {result}")
        existing = {}
        if RESULTS_PATH.exists():
            existing = json.loads(RESULTS_PATH.read_text())
        existing["drift_under_load"] = result
        RESULTS_PATH.write_text(json.dumps(existing, indent=2, sort_keys=True))
    finally:
        stack.raw_store.close()
