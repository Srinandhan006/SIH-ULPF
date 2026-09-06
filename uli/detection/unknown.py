"""Unknown-source engine (Tier 7 support): clusters unknown events by family_hash, tracks inferred
field statistics, and once a cluster has enough events, proposes a candidate parser (delegates
synthesis to drift.evolution for the actual YAML, since the mechanism is identical: template →
typed alignment → labeled draft — here aligned against nothing, i.e. fresh field inference)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from uli.ids import ulid
from uli.models import IR, ParseContext


def record_unknown(storage, tenant_id: str, source_id: str, ctx: ParseContext, ir: IR, raw_event_id: str, min_events_for_suggestion: int) -> None:
    fam = ctx.fingerprint.family_hash
    existing = storage.find_unknown_cluster(tenant_id, source_id, fam)
    now = datetime.now(timezone.utc)
    inferred = _merge_inferred(existing.get("inferred_fields") if existing else {}, ir)
    shape_hashes = set(existing.get("shape_hashes") or []) if existing else set()
    shape_hashes.add(ctx.fingerprint.shape_hash)
    template_ids = set(existing.get("template_ids") or []) if existing else set()
    if ir.template_id:
        template_ids.add(ir.template_id)
    templates = dict(existing.get("templates") or {}) if existing else {}
    if ir.template_id and ir.template:
        templates[ir.template_id] = ir.template
    count = (existing.get("event_count") if existing else 0) + 1
    cluster_id = existing["cluster_id"] if existing else ulid()
    status = existing.get("status", "open") if existing else "open"
    storage.upsert_unknown_cluster(
        cluster_id=cluster_id, tenant_id=tenant_id, source_id=source_id, family_hash=fam,
        shape_hashes=sorted(shape_hashes)[:100], template_ids=sorted(template_ids)[:100], templates=templates,
        inferred_fields=inferred, candidate_parsers=(existing or {}).get("candidate_parsers") or [],
        confidence=ir.confidence, status=status, event_count=count,
        example_raw_event_id=(existing or {}).get("example_raw_event_id") or raw_event_id,
        first_seen=(existing or {}).get("first_seen") or now, last_seen=now,
    )
    if count == min_events_for_suggestion and status == "open":
        return cluster_id  # caller may trigger suggestion synthesis
    return None


def _merge_inferred(prev: dict[str, Any] | None, ir: IR) -> dict[str, Any]:
    out = dict(prev or {})
    for k, v in ir.fields.items():
        if k.startswith("__") or v in (None, ""):
            continue
        e = out.setdefault(k, {"fill": 0, "example": str(v)[:200], "type": type(v).__name__})
        e["fill"] = e.get("fill", 0) + 1
    return out
