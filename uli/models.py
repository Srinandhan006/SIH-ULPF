"""Core data contracts (see docs/data-model.md). Pydantic v2 for validation at boundaries;
plain dataclasses on the hot path where validation would be redundant."""
from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import IntEnum
from typing import Any

from pydantic import BaseModel, Field

from uli import ENVELOPE_SCHEMA_VERSION, NORMALIZER_VERSION, OCSF_VERSION
from uli.ids import ulid


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def sha256_hex(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


# --------------------------------------------------------------------------- transport
class RawEnvelope(BaseModel):
    """What the collector/API knows before any parsing. Wire format on the queue."""

    tenant_id: str = "default"
    project_id: str = "default"
    source_id: str
    transport: str = "http"
    received_at: datetime = Field(default_factory=utcnow)
    peer: str | None = None
    payload_b64: str
    payload_sha256: str | None = None
    hints: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_bytes(cls, payload: bytes, **kw: Any) -> "RawEnvelope":
        return cls(payload_b64=base64.b64encode(payload).decode("ascii"), payload_sha256=sha256_hex(payload), **kw)

    def payload(self) -> bytes:
        return base64.b64decode(self.payload_b64)


@dataclass(slots=True)
class RawRecord:
    raw_event_id: str
    tenant_id: str
    source_id: str
    transport: str
    peer: str | None
    received_at: datetime
    payload: bytes

    @property
    def length(self) -> int:
        return len(self.payload)


@dataclass(slots=True)
class RawLocation:
    segment: str
    offset: int
    length: int


# --------------------------------------------------------------------------- parsing
class TokenType(IntEnum):
    PUNCT = 0
    WORD = 1
    NUM = 2
    HEX = 3
    IP4 = 4
    IP6 = 5
    MAC = 6
    TS = 7
    UUID = 8
    URL = 9
    EMAIL = 10
    PATH = 11
    KV = 12
    QUOTE = 13
    PORT = 14
    HOST = 15


@dataclass(slots=True)
class Fingerprint:
    shape: str
    shape_hash: str
    family_hash: str
    tokens: list[str]
    classes: list[TokenType]
    token_count: int


@dataclass(slots=True)
class Observable:
    name: str
    type_id: int  # OCSF observable type_id
    value: str


@dataclass(slots=True)
class IR:
    """Intermediate representation: parser output before OCSF normalization."""

    fields: dict[str, Any] = field(default_factory=dict)
    typed: dict[str, TokenType] = field(default_factory=dict)
    observables: list[Observable] = field(default_factory=list)
    timestamp: datetime | None = None
    severity: int | None = None
    message: str = ""
    template_id: str | None = None
    template: str | None = None
    params: list[str] = field(default_factory=list)
    tier: int = 7
    confidence: float = 0.0
    confidence_factors: dict[str, float] = field(default_factory=dict)
    parser_id: str = "none"
    parser_version: str = "0"
    class_uid: int | None = None
    activity_id: int | None = None
    vendor: str | None = None
    product: str | None = None
    product_version: str | None = None
    errors: list[str] = field(default_factory=list)
    candidates: list[dict[str, Any]] = field(default_factory=list)
    ml: dict[str, Any] = field(default_factory=dict)
    shadow: dict[str, Any] | None = None


@dataclass(slots=True)
class ParseContext:
    raw: bytes
    text: str
    fingerprint: Fingerprint
    tenant_id: str
    source_id: str
    transport: str
    hints: dict[str, Any]
    truncated: bool = False


# --------------------------------------------------------------------------- normalized
class Provenance(BaseModel):
    raw_event_id: str
    raw_segment: str
    raw_offset: int
    raw_length: int
    received_at: datetime
    processed_at: datetime
    transport: str
    peer: str | None = None
    parser_id: str
    parser_version: str
    normalizer_version: str = NORMALIZER_VERSION
    template_id: str | None = None
    tier: int
    confidence: float
    confidence_factors: dict[str, float] = Field(default_factory=dict)
    shape_hash: str
    family_hash: str
    duplicate_of: str | None = None
    shadow_of: str | None = None
    processing_errors: list[str] = Field(default_factory=list)


class NormalizedEvent(BaseModel):
    event_id: str = Field(default_factory=ulid)
    schema_version: str = ENVELOPE_SCHEMA_VERSION
    tenant_id: str
    project_id: str = "default"
    source_id: str
    provenance: Provenance
    ocsf: dict[str, Any]
    message: str = ""
    anomaly: dict[str, Any] = Field(default_factory=lambda: {"score": None, "model": None, "suppressed_reason": None})

    @property
    def ocsf_version(self) -> str:
        return self.ocsf.get("metadata", {}).get("version", OCSF_VERSION)


# --------------------------------------------------------------------------- drift
class DriftEvent(BaseModel):
    drift_id: str = Field(default_factory=ulid)
    tenant_id: str = "default"
    kind: str
    key: str
    desired: Any
    observed: Any
    severity: str = "medium"
    detected_at: datetime = Field(default_factory=utcnow)
    snapshot_raw_event_id: str | None = None
    desired_state_sha256: str | None = None


class ParserDriftEvent(BaseModel):
    pdrift_id: str = Field(default_factory=ulid)
    tenant_id: str
    source_id: str
    parser_id: str
    parser_version: str
    detected_at: datetime = Field(default_factory=utcnow)
    js_divergence: float
    confidence_before: float
    confidence_after: float
    reference_histogram: dict[str, int]
    current_histogram: dict[str, int]
    window_size: int
    explained_by: list[str] = Field(default_factory=list)
    suggestion_id: str | None = None
    reason: str = ""
