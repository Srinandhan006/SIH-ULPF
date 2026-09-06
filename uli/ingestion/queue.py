"""Queue abstraction: Redis Streams (distributed) or in-process (local). Fail-soft: if Redis is
configured but unreachable at startup, we fall back to memory and say so loudly in metrics/logs."""
from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass
from typing import Iterator, Protocol

import orjson

from uli.logging import get_logger
from uli.metrics import QUEUE_DEPTH
from uli.models import RawEnvelope

log = get_logger("queue")


@dataclass(slots=True)
class Message:
    id: str
    envelope: RawEnvelope


class Queue(Protocol):
    name: str
    def publish(self, env: RawEnvelope) -> str: ...
    def publish_many(self, envs: list[RawEnvelope]) -> int: ...
    def consume(self, consumer: str, block_ms: int = 1000, count: int = 100) -> Iterator[list[Message]]: ...
    def ack(self, ids: list[str]) -> None: ...
    def depth(self) -> int: ...
    def health(self) -> dict: ...


class MemoryQueue:
    name = "memory"

    def __init__(self, maxsize: int = 100_000):
        self._q: queue.Queue[Message] = queue.Queue(maxsize=maxsize)
        self._seq = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()

    def publish(self, env: RawEnvelope) -> str:
        with self._lock:
            self._seq += 1
            mid = f"m-{self._seq}"
        self._q.put(Message(mid, env))
        QUEUE_DEPTH.set(self._q.qsize())
        return mid

    def publish_many(self, envs: list[RawEnvelope]) -> int:
        for e in envs:
            self.publish(e)
        return len(envs)

    def consume(self, consumer: str, block_ms: int = 1000, count: int = 100) -> Iterator[list[Message]]:
        while not self._stop.is_set():
            batch: list[Message] = []
            try:
                batch.append(self._q.get(timeout=block_ms / 1000))
            except queue.Empty:
                yield []
                continue
            while len(batch) < count:
                try:
                    batch.append(self._q.get_nowait())
                except queue.Empty:
                    break
            QUEUE_DEPTH.set(self._q.qsize())
            yield batch

    def ack(self, ids: list[str]) -> None:
        return None

    def depth(self) -> int:
        return self._q.qsize()

    def health(self) -> dict:
        return {"ok": True, "backend": "memory", "depth": self.depth()}

    def stop(self) -> None:
        self._stop.set()


class RedisStreamsQueue:
    name = "redis"

    def __init__(self, url: str, stream: str, group: str, maxlen: int = 1_000_000):
        import redis  # local import: optional at runtime

        self.r = redis.Redis.from_url(url, socket_timeout=5, socket_connect_timeout=3)
        self.stream, self.group, self.maxlen = stream, group, maxlen
        self.r.ping()
        try:
            self.r.xgroup_create(self.stream, self.group, id="0", mkstream=True)
        except redis.ResponseError as e:  # BUSYGROUP = already exists
            if "BUSYGROUP" not in str(e):
                raise
        self._stop = threading.Event()

    def publish(self, env: RawEnvelope) -> str:
        return self.r.xadd(self.stream, {"e": env.model_dump_json()}, maxlen=self.maxlen, approximate=True).decode()

    def publish_many(self, envs: list[RawEnvelope]) -> int:
        p = self.r.pipeline(transaction=False)
        for e in envs:
            p.xadd(self.stream, {"e": e.model_dump_json()}, maxlen=self.maxlen, approximate=True)
        p.execute()
        return len(envs)

    def consume(self, consumer: str, block_ms: int = 1000, count: int = 100) -> Iterator[list[Message]]:
        # First drain any pending (un-acked) messages for this consumer (crash recovery), then new ones.
        first = True
        while not self._stop.is_set():
            try:
                stream_id = "0" if first else ">"
                resp = self.r.xreadgroup(self.group, consumer, {self.stream: stream_id}, count=count, block=block_ms)
                first = False
            except Exception as e:  # noqa: BLE001
                log.warning("redis_read_failed", error=str(e)[:200])
                time.sleep(1.0)
                yield []
                continue
            batch: list[Message] = []
            for _, msgs in resp or []:
                for mid, fields in msgs:
                    try:
                        batch.append(Message(mid.decode(), RawEnvelope.model_validate(orjson.loads(fields[b"e"]))))
                    except Exception as e:  # noqa: BLE001  poison message: ack & log, never stall
                        log.error("poison_message", id=mid, error=str(e)[:200])
                        self.r.xack(self.stream, self.group, mid)
            try:
                QUEUE_DEPTH.set(self.r.xlen(self.stream))
            except Exception:
                pass
            yield batch

    def ack(self, ids: list[str]) -> None:
        if ids:
            self.r.xack(self.stream, self.group, *ids)

    def depth(self) -> int:
        try:
            return int(self.r.xlen(self.stream))
        except Exception:
            return -1

    def health(self) -> dict:
        try:
            self.r.ping()
            return {"ok": True, "backend": "redis", "depth": self.depth()}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "backend": "redis", "error": str(e)[:200]}

    def stop(self) -> None:
        self._stop.set()


_shared_memory_queue: MemoryQueue | None = None


def build_queue(settings) -> Queue:
    """auto: redis if reachable else memory. Memory queue is process-local (singleton)."""
    global _shared_memory_queue
    backend = settings.queue_backend
    if backend in ("auto", "redis"):
        try:
            q = RedisStreamsQueue(settings.redis_url, settings.stream_name, settings.consumer_group, settings.stream_maxlen)
            log.info("queue_backend", backend="redis", url=settings.redis_url)
            return q
        except Exception as e:  # noqa: BLE001
            if backend == "redis":
                raise
            log.warning("redis_unavailable_falling_back_to_memory", error=str(e)[:200])
    if _shared_memory_queue is None:
        _shared_memory_queue = MemoryQueue()
    log.info("queue_backend", backend="memory")
    return _shared_memory_queue
