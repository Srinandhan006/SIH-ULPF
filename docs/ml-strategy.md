# ML strategy — necessity, scope, and cost

The project brief requires evaluating whether ML is *necessary* rather than adding it by default.
This doc states where ML sits in the ladder, why it is tier 6 (optional, after five non-ML tiers
already produce an event), and the measured cost of including it.

## 1. Where ML sits, and why it is optional

The 7-tier ladder (`architecture.md` §4) already guarantees every line produces an OCSF event
without any ML: structural (1) → declarative vendor pack (2) → field inference (3) → Drain3
template mining (4) → shape similarity (5) → **ML sidecar (6, optional)** → quarantine (7). ML is
consulted only for lines that reached tier 6 — i.e. lines four independent non-ML strategies
already failed to confidently parse. This ordering is deliberate: ML adds latency, a model
dependency, and a failure mode (silent drift in model quality) that deterministic tiers do not
have, so it earns its place only where cheaper methods are exhausted, and it never gates ingestion
(`uli/ml/service.py`'s `/v1/infer` catches all exceptions and returns `{}` rather than ever
raising — "must never 500 the caller's pipeline").

## 2. What the ML sidecar actually does

Two small, CPU-only, no-pretrained-weights models (`uli/ml/`), both trained online from traffic the
deployment itself sees — no model download, which matters for air-gapped operation (`ULI_ML_ENABLED=false`
by default; enabling it never requires network access):

- **`SourceAnomalyModel`** (`uli/ml/anomaly.py`): one `IsolationForest` per `source_id`, trained
  incrementally (`min_train=50`, `retrain_every=200`) on hashed shape n-gram + structural-stat
  features (`uli/ml/features.py` — 256 hashed buckets + 4 structural stats, no PII, no raw tokens
  retained). Produces `anomaly_score` in (0,1). Annotation only — never blocks or reclassifies an
  event's tier.
- **`NearestCentroidClassifier`** (`uli/ml/vendor_classifier.py`): self-supervised from routing
  hits (`known_label` passed in when a deterministic tier already identified the vendor), used to
  produce a `vendor_guess`/`vendor_similarity` hint for otherwise-unidentified lines. Also
  annotation only.

Both fail closed: `uli/ml/service.py:infer` wraps everything in a single `try/except` and returns
whatever partial dict it managed to build, never an error to the caller. The primary ingestion
path calls this over HTTP with `ml_timeout_ms=20` and a circuit breaker
(`ml_circuit_failures`/`ml_circuit_reset_s`) — a slow or dead ML sidecar degrades to "tier 6
produces nothing extra", not a stall.

## 3. Why a separate container, and the measured cost

`Dockerfile.ml` is a distinct image so the primary `api`/`worker` containers never pay for
`scikit-learn`/`numpy`/`scipy`/`joblib` unless ML is explicitly enabled. Measured image sizes
(`docker images`, this build, `python:3.12-slim` base for all three):

| Image | Size | Build time (this run) |
|---|---|---|
| `uli-api` | 441 MB | ~112s |
| `uli-worker` | 422 MB | ~29s (cache-assisted) |
| `uli-ml` | 687 MB | ~146s |
| `uli-collector` (Go, distroless) | 17.9 MB | ~58s |

The ML sidecar is **56% larger** than the base API image for a feature that only ever fires on
already-unidentified traffic — this is the concrete evidence behind making it `ml` a Docker Compose
*profile* (`docker compose --profile ml up`) rather than part of the default stack, and behind
`ml_enabled: bool = False` as the config default.

## 4. What this is / is not

Per `research.md` §6/§9: Isolation Forest for log anomaly scoring and nearest-centroid /
few-shot vendor classification are both standard, well-published techniques — not novel. No
neural/LLM-based parsing is used anywhere in the ladder (per `research.md` §2.3's evaluation:
higher latency and cost, and non-air-gap-friendly for most hosted options, for a job the
deterministic+statistical tiers already do at tiers 1-5). The honest claim is narrower: this
system demonstrates that ML is *not required* to satisfy the core requirement (every line produces
a traceable event) and confines ML to a bounded, optional, fail-open annotation role — which is a
scoping decision, not a modeling contribution.

## 5. Known limitations

- Per-source `IsolationForest` models are in-memory (`AnomalyPool`) and rebuilt from a bounded
  ring buffer (`deque(maxlen=2000)`) — a worker restart loses trained state; no persistence to disk
  is wired up yet despite `ml_models_dir` existing in config (roadmap item).
- No offline evaluation harness (precision/recall against labeled anomalies) exists yet; anomaly
  scores are exposed for operator judgment, not asserted to be accurate.
