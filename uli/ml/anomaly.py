"""Online anomaly scorer: Isolation Forest over hashed shape features (docs/ml-strategy.md,
docs/architecture.md P4 — annotate only, never blocks). Trains incrementally in small batches on
whatever has been seen per-source; ships with scikit-learn, CPU, no GPU, air-gap safe."""
from __future__ import annotations

import threading
from collections import deque
from pathlib import Path

import joblib
import numpy as np
from sklearn.ensemble import IsolationForest

from uli.ml.features import featurize


class SourceAnomalyModel:
    def __init__(self, min_train: int = 50, retrain_every: int = 200, contamination: float = 0.02):
        self.min_train, self.retrain_every, self.contamination = min_train, retrain_every, contamination
        self._buffer: deque[np.ndarray] = deque(maxlen=2000)
        self._model: IsolationForest | None = None
        self._since_retrain = 0

    def score(self, shape: str, tokens: list[str]) -> float | None:
        x = featurize(shape, tokens)
        self._buffer.append(x)
        self._since_retrain += 1
        if self._model is None:
            if len(self._buffer) >= self.min_train:
                self._fit()
            return None
        if self._since_retrain >= self.retrain_every:
            self._fit()
        try:
            raw = self._model.score_samples(x.reshape(1, -1))[0]
            return float(1.0 / (1.0 + np.exp(raw * 5)))  # squashed to (0,1), higher = more anomalous
        except Exception:  # noqa: BLE001 — scoring must never raise
            return None

    def _fit(self) -> None:
        X = np.vstack(self._buffer)
        m = IsolationForest(n_estimators=100, contamination=self.contamination, random_state=0, n_jobs=1)
        m.fit(X)
        self._model = m
        self._since_retrain = 0


class AnomalyPool:
    def __init__(self, models_dir: Path):
        self.models_dir = Path(models_dir)
        self.models_dir.mkdir(parents=True, exist_ok=True)
        self._models: dict[str, SourceAnomalyModel] = {}
        self._lock = threading.Lock()

    def get(self, source_id: str) -> SourceAnomalyModel:
        with self._lock:
            m = self._models.get(source_id)
            if m is None:
                m = SourceAnomalyModel()
                self._models[source_id] = m
        return m

    def score(self, source_id: str, shape: str, tokens: list[str]) -> float | None:
        return self.get(source_id).score(shape, tokens)
