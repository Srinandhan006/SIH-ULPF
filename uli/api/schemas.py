from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class IngestRequest(BaseModel):
    source_id: str
    transport: str = "http"
    lines: list[str] | None = None
    text: str | None = None
    hints: dict[str, Any] = Field(default_factory=dict)


class IngestResponse(BaseModel):
    accepted: int
    queued: bool
    event_ids: list[str] = Field(default_factory=list)


class PromoteRequest(BaseModel):
    approved_by: str = "admin"


class BundleRequest(BaseModel):
    yaml: str
    signature: str | None = Field(None, description="base64 Ed25519 signature over the yaml field's UTF-8 bytes")


class ParserSpecResponse(BaseModel):
    parser_id: str
    version: str
    kind: str
    vendor: str | None = None
    product: str | None = None
    status: str
