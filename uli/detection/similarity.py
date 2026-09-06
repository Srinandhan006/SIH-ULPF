"""Tier-5: similarity search against known-parser fingerprints/templates using shape 3-gram
Jaccard (H3 routing-cache primitive reused here). No embedding model required — CPU, air-gap safe."""
from __future__ import annotations

from dataclasses import dataclass

from uli.fingerprint import jaccard, shape_ngrams


@dataclass(slots=True)
class SimilarityIndex:
    """In-memory index: parser_id -> list of reference shape n-gram sets (from onboarding samples
    or accumulated live matches). Rebuilt cheaply; persisted via storage.upsert_fingerprint history."""

    entries: dict[str, list[set[str]]]

    @classmethod
    def empty(cls) -> "SimilarityIndex":
        return cls(entries={})

    def add_reference(self, parser_id: str, shape: str) -> None:
        self.entries.setdefault(parser_id, [])
        ng = shape_ngrams(shape)
        if not any(jaccard(ng, existing) > 0.95 for existing in self.entries[parser_id]):
            self.entries[parser_id].append(ng)
            if len(self.entries[parser_id]) > 200:
                self.entries[parser_id].pop(0)

    def best_match(self, shape: str) -> tuple[str, float] | None:
        ng = shape_ngrams(shape)
        best_id, best_score = None, 0.0
        for pid, refs in self.entries.items():
            for ref in refs:
                s = jaccard(ng, ref)
                if s > best_score:
                    best_id, best_score = pid, s
        return (best_id, best_score) if best_id else None
