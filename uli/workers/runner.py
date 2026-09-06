"""Worker: consume RawEnvelopes from the queue, run the pipeline, ack. Never stalls on one bad
message (poison messages are ack'd and logged by the queue layer itself); never stalls on a
processing exception (pipeline.process is total)."""
from __future__ import annotations

import signal
import socket
import sys
import time

from uli.bootstrap import build_stack
from uli.logging import get_logger

log = get_logger("worker")


def main() -> None:
    stack = build_stack()
    consumer_name = f"{socket.gethostname()}-{sys.argv[1] if len(sys.argv) > 1 else '0'}"
    stop = {"flag": False}

    def _sigterm(signum, frame):
        stop["flag"] = True
        log.info("worker_stopping", signal=signum)

    signal.signal(signal.SIGTERM, _sigterm)
    signal.signal(signal.SIGINT, _sigterm)

    log.info("worker_started", consumer=consumer_name, queue=stack.queue.name)
    last_resource_tick = 0.0
    for batch in stack.queue.consume(consumer_name, block_ms=1000, count=200):
        if stop["flag"]:
            break
        ids = []
        for msg in batch:
            try:
                stack.pipeline.process(msg.envelope)
            except Exception as e:  # noqa: BLE001 — pipeline.process is documented total; this is a last-resort log
                log.error("worker_process_unexpected_exception", error=str(e)[:300])
            ids.append(msg.id)
        if ids:
            stack.queue.ack(ids)
        now = time.monotonic()
        if now - last_resource_tick > stack.settings.resource_drift_interval_s:
            try:
                stack.resource_drift.tick()
            except Exception as e:  # noqa: BLE001
                log.warning("resource_drift_tick_failed", error=str(e)[:200])
            last_resource_tick = now
    stack.engine.templates.save_all()
    log.info("worker_stopped")


if __name__ == "__main__":
    main()
