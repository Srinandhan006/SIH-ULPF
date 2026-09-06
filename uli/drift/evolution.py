"""Hypothesis H1: LLM-free parser evolution by label transfer (docs/architecture.md §5.2,
docs/parser-design.md §7). Needleman-Wunsch alignment between the old parser's reference template
and a newly-mined Drain3 template, transferring field labels across the alignment to synthesize a
candidate declarative pack — entirely offline, CPU-only, deterministic."""
from __future__ import annotations

from dataclasses import dataclass

from uli.fingerprint import classify, tokenize
from uli.models import TokenType

_COMPAT = {
    (TokenType.NUM, TokenType.PORT), (TokenType.PORT, TokenType.NUM),
    (TokenType.WORD, TokenType.HOST), (TokenType.HOST, TokenType.WORD),
    (TokenType.NUM, TokenType.HEX), (TokenType.HEX, TokenType.NUM),
}


def _cost(a: TokenType, b: TokenType) -> int:
    if a == b:
        return 2
    if (a, b) in _COMPAT:
        return 1
    return -2


@dataclass(slots=True)
class AlignedPosition:
    a_index: int | None
    b_index: int | None


def align(a_tokens: list[str], b_tokens: list[str]) -> list[AlignedPosition]:
    """Needleman-Wunsch global alignment on token *types*, gap penalty -1. O(n*m); n,m are typically
    < 60 tokens per log line, so this is cheap even run per drifted template (not per event)."""
    a_cls = [classify(t) for t in a_tokens]
    b_cls = [classify(t) for t in b_tokens]
    n, m = len(a_cls), len(b_cls)
    GAP = -1
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        dp[i][0] = dp[i - 1][0] + GAP
    for j in range(1, m + 1):
        dp[0][j] = dp[0][j - 1] + GAP
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            diag = dp[i - 1][j - 1] + _cost(a_cls[i - 1], b_cls[j - 1])
            up = dp[i - 1][j] + GAP
            left = dp[i][j - 1] + GAP
            dp[i][j] = max(diag, up, left)
    i, j = n, m
    out: list[AlignedPosition] = []
    while i > 0 or j > 0:
        if i > 0 and j > 0 and dp[i][j] == dp[i - 1][j - 1] + _cost(a_cls[i - 1], b_cls[j - 1]):
            out.append(AlignedPosition(i - 1, j - 1))
            i, j = i - 1, j - 1
        elif i > 0 and dp[i][j] == dp[i - 1][j] + GAP:
            out.append(AlignedPosition(i - 1, None))
            i -= 1
        else:
            out.append(AlignedPosition(None, j - 1))
            j -= 1
    out.reverse()
    return out


def transfer_labels(old_template_tokens: list[str], old_positions: dict[int, str], new_template_tokens: list[str]) -> tuple[dict[int, str], float]:
    """old_positions: token index (in old_template_tokens) -> field name that a declarative pack's
    extraction assigned there. Returns (new_index -> field name, score = carried / total_labeled)."""
    a_cls = [classify(t) for t in old_template_tokens]
    b_cls = [classify(t) for t in new_template_tokens]
    alignment = align(old_template_tokens, new_template_tokens)
    new_positions: dict[int, str] = {}
    carried = 0
    for ap in alignment:
        if ap.a_index is None or ap.b_index is None:
            continue
        name = old_positions.get(ap.a_index)
        if name is None:
            continue
        if a_cls[ap.a_index] == b_cls[ap.b_index] or (a_cls[ap.a_index], b_cls[ap.b_index]) in _COMPAT:
            new_positions[ap.b_index] = name
            carried += 1
    total = len(old_positions) or 1
    return new_positions, carried / total


def synthesize_pack_yaml(base_spec: dict, new_positions: dict[int, str], new_template_tokens: list[str], score: float) -> str:
    """Build a positional-extraction declarative pack candidate from the transferred labels. This is
    intentionally simple (positional extraction on tokenized text) — good enough as a *draft* an
    administrator reviews and can refine; it is never auto-promoted (docs/architecture.md §5.1)."""
    import yaml as _yaml

    spec = dict(base_spec)
    old_version = spec.get("version", "1.0.0")
    major, minor, patch = (int(x) for x in old_version.split("."))
    spec["version"] = f"{major}.{minor + 1}.0"
    spec["parser_id"] = base_spec["parser_id"]
    spec["description"] = (base_spec.get("description", "") + f" [auto-evolved candidate, label-transfer score={score:.2f}]").strip()
    positions_map = {str(idx): name for idx, name in sorted(new_positions.items())}
    spec["extract"] = [{"kind": "positional", "field": "message", "positions": positions_map}]
    spec.setdefault("confidence", {})["base"] = round(max(0.5, min(0.85, score)), 2)
    spec["provenance"] = {"origin": "drift_evolution", "score": round(score, 4), "based_on_version": old_version, "new_template_preview": " ".join(new_template_tokens[:40])}
    return _yaml.safe_dump(spec, sort_keys=False)
