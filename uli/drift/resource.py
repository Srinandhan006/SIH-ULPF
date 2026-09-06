"""Resource/configuration drift: desired-state.yaml vs observed (psutil, Docker Engine API,
optionally Kubernetes API). Desired -> Observed -> Diff -> DriftEvent (docs/architecture.md §5.3)."""
from __future__ import annotations

import hashlib
import json
import os
import socket
from pathlib import Path
from typing import Any

import psutil
import yaml

from uli.ids import ulid
from uli.logging import get_logger
from uli.metrics import DRIFT_EVENTS
from uli.models import DriftEvent, RawRecord

log = get_logger("drift.resource")


def load_desired_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text()) or {}


def observe_system() -> dict[str, Any]:
    obs: dict[str, Any] = {
        "cpu_count": psutil.cpu_count(logical=True) or 0,
        "memory_gb": round(psutil.virtual_memory().total / (1024**3), 2),
        "hostname": socket.gethostname(),
    }
    try:
        du = psutil.disk_usage("/")
        obs["disk_total_gb"] = round(du.total / (1024**3), 2)
        obs["disk_used_pct"] = round(du.percent, 1)
    except OSError:
        pass
    obs["env"] = {k: v for k, v in os.environ.items() if k.startswith("ULI_")}
    obs.update(_docker_state())
    return obs


def _docker_state() -> dict[str, Any]:
    sock = Path("/var/run/docker.sock")
    if not sock.exists():
        return {}
    try:
        import httpx

        transport = httpx.HTTPTransport(uds=str(sock))
        with httpx.Client(transport=transport, base_url="http://docker") as c:
            r = c.get("/containers/json", params={"all": "false"}, timeout=2.0)
            r.raise_for_status()
            containers = r.json()
        images = {}
        for c_ in containers:
            name = c_.get("Names", ["?"])[0].lstrip("/")
            images[name] = {"image": c_.get("Image"), "image_id": c_.get("ImageID"), "state": c_.get("State")}
        return {"containers": images}
    except Exception as e:  # noqa: BLE001 — resource observation must never crash the agent
        log.debug("docker_probe_failed", error=str(e)[:200])
        return {}


def diff(desired: dict[str, Any], observed: dict[str, Any]) -> list[DriftEvent]:
    events: list[DriftEvent] = []

    def check(kind: str, key: str, want: Any, got: Any, severity: str = "medium") -> None:
        if want is None:
            return
        if want != got:
            events.append(DriftEvent(kind=kind, key=key, desired=want, observed=got, severity=severity))

    check("cpu", "cpu_count", desired.get("cpu_count"), observed.get("cpu_count"), "low")
    check("memory", "memory_gb", desired.get("memory_gb"), observed.get("memory_gb"), "low")
    for name, spec in (desired.get("containers") or {}).items():
        got = (observed.get("containers") or {}).get(name)
        if got is None:
            events.append(DriftEvent(kind="config", key=f"containers.{name}", desired=spec, observed=None, severity="high"))
            continue
        if spec.get("image_digest") and spec["image_digest"] != got.get("image_id"):
            events.append(DriftEvent(kind="image", key=f"containers.{name}.image_id", desired=spec.get("image_digest"), observed=got.get("image_id"), severity="high"))
    for k, v in (desired.get("env") or {}).items():
        got = (observed.get("env") or {}).get(k)
        check("env", f"env.{k}", v, got, "medium")
    return events


def snapshot_hash(observed: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(observed, sort_keys=True, default=str).encode()).hexdigest()


def desired_state_hash(desired: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(desired, sort_keys=True, default=str).encode()).hexdigest()


class ResourceDriftAgent:
    def __init__(self, settings, storage, raw_store):
        self.settings, self.storage, self.raw_store = settings, storage, raw_store

    def tick(self) -> list[DriftEvent]:
        desired = load_desired_state(self.settings.desired_state_path)
        observed = observe_system()
        snap = json.dumps(observed, sort_keys=True, default=str).encode()
        from uli.models import utcnow

        raw_id = hashlib.sha256(snap).hexdigest()
        loc = self.raw_store.append(RawRecord(raw_event_id=raw_id, tenant_id=self.settings.default_tenant, source_id="drift-agent", transport="internal", peer=None, received_at=utcnow(), payload=snap))
        self.storage.index_raw(RawRecord(raw_event_id=raw_id, tenant_id=self.settings.default_tenant, source_id="drift-agent", transport="internal", peer=None, received_at=utcnow(), payload=snap), loc)
        events = diff(desired, observed)
        for ev in events:
            ev.tenant_id = self.settings.default_tenant
            ev.snapshot_raw_event_id = raw_id
            ev.desired_state_sha256 = desired_state_hash(desired)
            self.storage.write_drift(ev)
            DRIFT_EVENTS.labels(kind=ev.kind, severity=ev.severity).inc()
            log.warning("resource_drift", kind=ev.kind, key=ev.key, desired=str(ev.desired)[:100], observed=str(ev.observed)[:100])
        return events
