"""Feature extraction shared by training and inference. Deliberately simple, CPU, no embedding
model — hashed shape n-grams + structural stats (docs/ml-strategy.md). No PII in features."""
from __future__ import annotations

import numpy as np

from uli.fingerprint import shape_ngrams

N_HASH_BUCKETS = 256


def hash_features(shape: str) -> np.ndarray:
    v = np.zeros(N_HASH_BUCKETS, dtype=np.float32)
    for ng in shape_ngrams(shape, 3):
        v[hash(ng) % N_HASH_BUCKETS] += 1.0
    n = v.sum()
    return v / n if n > 0 else v


def struct_stats(tokens: list[str]) -> np.ndarray:
    if not tokens:
        return np.zeros(4, dtype=np.float32)
    lens = [len(t) for t in tokens]
    return np.array([len(tokens), float(np.mean(lens)), float(np.std(lens)), sum(1 for t in tokens if t.isdigit()) / len(tokens)], dtype=np.float32)


def featurize(shape: str, tokens: list[str]) -> np.ndarray:
    return np.concatenate([hash_features(shape), struct_stats(tokens)])
