"""Offline-trained vendor/shape classifier: nearest-centroid over hashed shape features, one
centroid per known parser_id, built from onboarding samples / live routing-cache hits. This is
Lane A ("decide offline, with a human check") from docs — used only as a *suggestion* signal for
unknown events (tier 6), never to auto-route with authority."""
from __future__ import annotations

from collections import defaultdict

import numpy as np

from uli.ml.features import featurize


class NearestCentroidClassifier:
    def __init__(self):
        self._sums: dict[str, np.ndarray] = {}
        self._counts: dict[str, int] = defaultdict(int)

    def update(self, label: str, shape: str, tokens: list[str]) -> None:
        x = featurize(shape, tokens)
        if label in self._sums:
            self._sums[label] += x
        else:
            self._sums[label] = x.copy()
        self._counts[label] += 1

    def predict(self, shape: str, tokens: list[str]) -> tuple[str, float] | None:
        if not self._sums:
            return None
        x = featurize(shape, tokens)
        best_label, best_sim = None, -1.0
        xn = x / (np.linalg.norm(x) + 1e-9)
        for label, s in self._sums.items():
            c = s / self._counts[label]
            cn = c / (np.linalg.norm(c) + 1e-9)
            sim = float(np.dot(xn, cn))
            if sim > best_sim:
                best_label, best_sim = label, sim
        return (best_label, best_sim) if best_label else None
