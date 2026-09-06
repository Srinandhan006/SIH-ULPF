"""IR → OCSF 1.9 NormalizedEvent. Total function: always returns a valid envelope, defaulting to
class_uid 0 (Base Event) when the parser/tiers didn't determine a class. Never discards data —
everything without an explicit mapping lands in ocsf.unmapped."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from uli import ENVELOPE_SCHEMA_VERSION, NORMALIZER_VERSION, OCSF_VERSION
from uli.models import IR, NormalizedEvent, Provenance, RawLocation
from uli.timestamps import to_epoch_ms

# Minimal OCSF category/class map we actively populate (extend as new packs are added).
_CLASS_META = {
    0: ("Base Event", 0),
    1001: ("File System Activity", 1),
    2004: ("Detection Finding", 2),
    4001: ("Network Activity", 4),
    4002: ("HTTP Activity", 4),
    4003: ("DNS Activity", 4),
    3002: ("Authentication", 3),
    5001: ("Device Config State", 5),
    6003: ("Web Resources Activity", 6),
}

_ALWAYS_RESERVED_FIELD_PREFIXES = ("__", "syslog.", "cef.", "leef.", "sd.", "inferred.")


def _ocsf_field(fields: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in fields.items():
        if k.startswith("ocsf."):
            _set_path(out, k[len("ocsf."):], v)
    return out


def _set_path(d: dict, path: str, value: Any) -> None:
    parts = path.split(".")
    cur = d
    for p in parts[:-1]:
        cur = cur.setdefault(p, {})
        if not isinstance(cur, dict):
            return
    cur[parts[-1]] = value


def _unmapped(fields: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for k, v in fields.items():
        if k.startswith("ocsf.") or k.startswith(_ALWAYS_RESERVED_FIELD_PREFIXES):
            continue
        if v is None:
            continue
        out[k] = v if isinstance(v, (str, int, float, bool)) else str(v)[:2000]
    return out


def _endpoints(fields: dict[str, Any], ocsf_body: dict[str, Any]) -> None:
    """Best-effort src/dst endpoints from inference (tier 3/4) when a declarative pack (tier 2)
    hasn't already set them explicitly."""
    if "src_endpoint" not in ocsf_body and fields.get("__src_ip"):
        ep: dict[str, Any] = {"ip": fields["__src_ip"]}
        if fields.get("__src_port"):
            try:
                ep["port"] = int(fields["__src_port"])
            except (ValueError, TypeError):
                pass
        ocsf_body["src_endpoint"] = ep
    if "dst_endpoint" not in ocsf_body and fields.get("__dst_ip"):
        ep = {"ip": fields["__dst_ip"]}
        if fields.get("__dst_port"):
            try:
                ep["port"] = int(fields["__dst_port"])
            except (ValueError, TypeError):
                pass
        ocsf_body["dst_endpoint"] = ep
    if "connection_info" not in ocsf_body and fields.get("__proto"):
        ocsf_body["connection_info"] = {"protocol_name": fields["__proto"]}


def normalize(
    ir: IR,
    *,
    tenant_id: str,
    project_id: str,
    source_id: str,
    raw_event_id: str,
    raw_loc: RawLocation,
    received_at: datetime,
    transport: str,
    peer: str | None,
    shape_hash: str,
    family_hash: str,
    embed_raw: bool,
    raw_bytes_for_embed: bytes | None = None,
    duplicate_of: str | None = None,
) -> NormalizedEvent:
    class_uid = ir.class_uid if ir.class_uid is not None else 0
    class_name, category_uid = _CLASS_META.get(class_uid, ("Base Event", 0))
    ts_dt = ir.timestamp or received_at
    ocsf_body: dict[str, Any] = _ocsf_field(ir.fields)
    ocsf_body.setdefault("class_uid", class_uid)
    ocsf_body.setdefault("class_name", class_name)
    ocsf_body.setdefault("category_uid", category_uid)
    if ir.activity_id is not None:
        ocsf_body.setdefault("activity_id", ir.activity_id)
    ocsf_body.setdefault("type_uid", class_uid * 100 + (ir.activity_id or 0))
    ocsf_body.setdefault("severity_id", ir.severity if ir.severity is not None else 0)
    ocsf_body["time"] = to_epoch_ms(ts_dt)
    metadata = ocsf_body.get("metadata") if isinstance(ocsf_body.get("metadata"), dict) else {}
    metadata.setdefault("version", OCSF_VERSION)
    if ir.vendor or ir.product:
        metadata.setdefault("product", {"vendor_name": ir.vendor, "name": ir.product, "version": ir.product_version})
    metadata.setdefault("original_time", ir.fields.get("__ts_original"))
    metadata.setdefault("uid", raw_event_id)
    ocsf_body["metadata"] = metadata
    _endpoints(ir.fields, ocsf_body)
    observables = [{"name": o.name, "type_id": o.type_id, "value": o.value} for o in ir.observables]
    if observables:
        ocsf_body["observables"] = observables
    unmapped = _unmapped(ir.fields)
    if unmapped:
        ocsf_body["unmapped"] = unmapped
    if embed_raw and raw_bytes_for_embed is not None:
        ocsf_body["raw_data"] = raw_bytes_for_embed.decode("utf-8", errors="replace")[:65536]
    else:
        ocsf_body["raw_data"] = None

    prov = Provenance(
        raw_event_id=raw_event_id, raw_segment=raw_loc.segment, raw_offset=raw_loc.offset, raw_length=raw_loc.length,
        received_at=received_at, processed_at=datetime.now(timezone.utc), transport=transport, peer=peer,
        parser_id=ir.parser_id, parser_version=ir.parser_version, normalizer_version=NORMALIZER_VERSION,
        template_id=ir.template_id, tier=ir.tier, confidence=round(ir.confidence, 4), confidence_factors=ir.confidence_factors,
        shape_hash=shape_hash, family_hash=family_hash, duplicate_of=duplicate_of, shadow_of=(ir.shadow or {}).get("of"),
        processing_errors=ir.errors,
    )
    return NormalizedEvent(
        schema_version=ENVELOPE_SCHEMA_VERSION, tenant_id=tenant_id, project_id=project_id, source_id=source_id,
        provenance=prov, ocsf=ocsf_body, message=ir.message[:8192],
        anomaly={"score": ir.ml.get("anomaly_score"), "model": ir.ml.get("anomaly_model"), "suppressed_reason": ir.ml.get("suppressed_reason")},
    )
