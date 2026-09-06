"""Parser interface. Every parser is a total function: can_parse/parse never raise."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import regex

from uli.models import IR, ParseContext


class ParserKind(str, Enum):
    STRUCTURAL = "structural"
    DECLARATIVE = "declarative"
    PYTHON = "python"
    SUGGESTED = "suggested"


@dataclass(slots=True)
class ParserMetadata:
    parser_id: str
    version: str
    kind: ParserKind
    tier: int
    vendor: str | None = None
    product: str | None = None
    signatures: list[dict[str, Any]] = field(default_factory=list)
    compat: dict[str, str] = field(default_factory=lambda: {"envelope": "uli.v1", "ocsf": ">=1.6 <2"})
    description: str = ""
    status: str = "active"


class Parser(ABC):
    id: str
    version: str = "1.0.0"
    kind: ParserKind = ParserKind.STRUCTURAL
    tier: int = 1
    vendor: str | None = None
    product: str | None = None

    @abstractmethod
    def can_parse(self, ctx: ParseContext) -> float: ...

    @abstractmethod
    def parse(self, ctx: ParseContext) -> IR: ...

    def metadata(self) -> ParserMetadata:
        return ParserMetadata(parser_id=self.id, version=self.version, kind=self.kind, tier=self.tier, vendor=self.vendor, product=self.product)

    # helpers -------------------------------------------------------------
    def new_ir(self, ctx: ParseContext, confidence: float) -> IR:
        return IR(message=ctx.text, tier=self.tier, confidence=confidence, parser_id=self.id, parser_version=self.version, vendor=self.vendor, product=self.product)


def safe_regex(pattern: str, flags: int = 0) -> regex.Pattern:
    return regex.compile(pattern, flags)


def guarded_search(pat: regex.Pattern, text: str, timeout_ms: int) -> regex.Match | None:
    """Regex with wall-clock timeout (the `regex` module supports it natively). Never raises."""
    try:
        return pat.search(text, timeout=timeout_ms / 1000.0)
    except TimeoutError:
        return None
    except Exception:
        return None
