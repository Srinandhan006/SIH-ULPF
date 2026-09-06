"""Optional ML sidecar (docs/architecture.md — tier 6). A separate HTTP service so the primary
ingestion container never carries scikit-learn/numpy unless ULI_ML_ENABLED=true (docs/roadmap:
image-size comparison). Endpoints: POST /v1/infer {shape, tokens, template} -> {anomaly_score,
vendor_guess, vendor_similarity}. Fits models online from what it sees; ships with none pre-trained
(air-gap: no model download; the "model" is data-driven from local traffic only)."""
from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel

from uli.config import get_settings
from uli.logging import configure, get_logger
from uli.ml.anomaly import AnomalyPool
from uli.ml.vendor_classifier import NearestCentroidClassifier

log = get_logger("ml")
settings = get_settings()
configure(settings.log_level, settings.log_json)

app = FastAPI(title="ULI ML Sidecar", version="0.1.0")
anomaly_pool = AnomalyPool(settings.ml_models_dir)
classifier = NearestCentroidClassifier()


class InferRequest(BaseModel):
    shape: str
    tokens: list[str] = []
    template: str | None = None
    source_id: str = "default"
    known_label: str | None = None  # when present, feeds the classifier (self-supervised from routing hits)


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.post("/v1/infer")
def infer(req: InferRequest) -> dict:
    out: dict = {}
    try:
        if req.known_label:
            classifier.update(req.known_label, req.shape, req.tokens)
        score = anomaly_pool.score(req.source_id, req.shape, req.tokens)
        if score is not None:
            out["anomaly_score"] = round(score, 4)
            out["anomaly_model"] = "isolation_forest_v1"
        guess = classifier.predict(req.shape, req.tokens)
        if guess:
            out["vendor_guess"], out["vendor_similarity"] = guess[0], round(guess[1], 4)
    except Exception as e:  # noqa: BLE001 — this endpoint must never 500 the caller's pipeline
        log.warning("ml_infer_error", error=str(e)[:200])
    return out


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8090, log_config=None)


if __name__ == "__main__":
    main()
