# Architecture

**Status:** Phase 2 deliverable · **Schema:** OCSF 1.9 + ULI provenance envelope v1 · **Stack:** Go (collector) + Python 3.12 (everything else)

## 1. Design principles

| # | Principle | Consequence |
|---|---|---|
| P1 | **Total pipeline.** Every stage is a total function `Event → Event`. | No stage may raise; errors become `processing_errors[]` on the event. Ingestion cannot stop because of content. |
| P2 | **Raw is truth.** Raw bytes are written first, immutably, content-addressed. | Normalization is a *derived view*; it can be recomputed by replaying raw. |
| P3 | **Graded, not binary.** Every event has `tier` and `confidence`. | Unknown vendors produce low-confidence events, never nothing. |
| P4 | **Deterministic hot path.** Anything that *decides* how to parse is deterministic and versioned. ML *scores* or *suggests*; it never blocks. | The core works with the ML service absent. |
| P5 | **Offline by construction.** No runtime fetch of packages, models, feeds. | Everything ships in the image or in a signed bundle. |
| P6 | **Boring technology.** Redis Streams, SQLite/PostgreSQL, FastAPI, Drain3, scikit-learn. | Swappable through adapters; no exotic ops burden. |

## 2. Component diagram

```
                          ┌──────────────────────────────────────────────────────────────┐
                          │                     LOG SOURCES                              │
                          │ syslog UDP/TCP · HTTP POST · file tail · docker/k8s stdout   │
                          └───────────────┬──────────────────────────┬───────────────────┘
                                          │ bytes                    │ bytes
                            ┌─────────────▼──────────────┐  ┌────────▼─────────┐
                            │ COLLECTOR (Go, `uli-collector`)│  │ API /ingest      │
                            │ syslog 3164/5424 framing,  │  │ (Python, FastAPI)│
                            │ octet-count + LF, file tail│  │                  │
                            │ → RawEnvelope              │  │ → RawEnvelope    │
                            └─────────────┬──────────────┘  └────────┬─────────┘
                                          │                          │
                                          └──────────┬───────────────┘
                                                     ▼
                           ┌───────────────────────────────────────────────┐
                           │  QUEUE  (Redis Streams `uli:raw`, consumer    │
                           │  groups; LOCAL mode = in-process asyncio)     │
                           └───────────────────────┬───────────────────────┘
                                                   ▼
        ┌──────────────────────────────────────────────────────────────────────────────────┐
        │ WORKER (Python, N replicas)                                                      │
        │                                                                                  │
        │  1. RawStore.append(bytes) ──► raw_event_id = sha256, segment, offset  (P2)     │
        │  2. Fingerprint  ──► shape_hash, family_hash, token classes                     │
        │  3. ParserEngine.route(fingerprint, bytes)                                       │
        │        L1 structural  L2 vendor signature  L3 field inference                    │
        │        L4 Drain3 templates  L5 similarity  L6 ML (optional)  L7 quarantine       │
        │        → IntermediateRepresentation (IR) + tier + confidence                    │
        │  4. Normalizer: IR ──► OCSF 1.9 class + envelope; schema validate (soft)         │
        │  5. DriftMonitor.observe(source_id, parser_id, shape_hash, confidence)           │
        │  6. Storage.write(NormalizedEvent) ; UnknownRegistry.record() if tier ≥ 5        │
        │  7. Metrics                                                                       │
        └───────────┬───────────────────────────┬───────────────────────────┬──────────────┘
                    │                           │                           │
        ┌───────────▼───────────┐   ┌───────────▼───────────┐   ┌───────────▼───────────┐
        │ RAW STORE (files)     │   │ EVENT STORE (SQL)     │   │ ML SERVICE (optional) │
        │ append-only segments  │   │ SQLite | PostgreSQL   │   │ vendor classifier     │
        │ content-addressed     │   │ events, sources,      │   │ anomaly scorer        │
        │ gzip-rotated          │   │ parsers, fingerprints,│   │ (HTTP, sidecar)       │
        │                       │   │ unknown, drift, audit │   │                       │
        └───────────────────────┘   └───────────┬───────────┘   └───────────────────────┘
                                                │
        ┌───────────────────────────────────────▼──────────────────────────────────────────┐
        │ API (FastAPI): /ingest /events /events/{id} /raw/{id} /sources /parsers          │
        │ /parsers/suggestions /parsers/{id}/promote /unknown /drift /health /metrics      │
        └──────────────────────────────────────────────────────────────────────────────────┘
        ┌──────────────────────────────────────────────────────────────────────────────────┐
        │ DRIFT AGENT: desired-state.yaml  vs  observed (psutil, docker socket, k8s API,   │
        │ registry versions, config hash) → Diff → DriftEvent (same provenance) →          │
        │ Correlator: joins with parser-drift events (±window) → `explained_by`            │
        └──────────────────────────────────────────────────────────────────────────────────┘
```

## 3. Data flow contracts

### 3.1 `RawEnvelope` (collector → queue)

Produced by the Go collector or the API. Contains only what the transport knows.

```json
{
  "tenant_id": "default",
  "source_id": "syslog:10.0.0.5",
  "transport": "syslog-udp",
  "received_at": "2026-09-05T10:00:00.123456Z",
  "peer": "10.0.0.5:514",
  "payload_b64": "PDM0PjEgMjAyNi0wOS0wNVQxMDowMDowMFogaG9zdCBhcHAgLSAtIC0gaGVsbG8=",
  "payload_sha256": "…",
  "hints": {"syslog_pri": 34}
}
```

The collector never parses beyond framing (it must be trivially correct). `payload_sha256` is recomputed by the worker; mismatch → `processing_errors += integrity_mismatch` (still processed).

### 3.2 `RawRecord` (raw store)

Immutable. `raw_event_id = sha256(payload)` — content-addressed, so byte-identical duplicates share an id (dedup is *detected*, not enforced: the normalized layer keeps one event per arrival, both pointing to the same raw id, with `duplicate_of` set on the later one).

```
segment file: logs/raw/<tenant>/<source>/<yyyy-mm-dd>/<segment_no>.ndjson[.gz]
line: {"raw_event_id","received_at","source_id","transport","payload_b64","len"}
index: SQL table raw_index(raw_event_id PK, tenant_id, source_id, segment, offset, length, received_at)
```

`GET /raw/{id}` seeks `segment:offset` and returns the original bytes with the stored hash so a client can verify.

### 3.3 `IntermediateRepresentation` (parser → normalizer)

```
IR {
  fields: dict[str, Any]          # flat, parser-native names (e.g. "srcip", "dst_port")
  typed:  dict[str, TokenType]    # inferred types per field (IP, PORT, TS, …)
  template_id: str | None         # Drain3 cluster id if used
  params: list[str]               # Drain3 parameters
  observables: list[Observable]   # ips, hosts, users, urls, hashes found anywhere
  timestamp: datetime | None      # best-effort event time
  severity: int | None
  message: str                    # human message (body)
  tier: int                       # 1..7
  confidence: float               # 0..1
  parser_id: str; parser_version: str
  errors: list[str]
}
```

### 3.4 `NormalizedEvent` (see `data-model.md`)

Envelope + OCSF body. Always produced.

## 4. Parsing ladder and confidence (summary; details in `parser-design.md`)

| Tier | Layer | Produces | Typical confidence |
|---|---|---|---|
| 1 | Structural detection (JSON, CEF, LEEF, syslog 5424/3164, logfmt, CLF, OTLP) | fields from the format's own grammar | 0.6–0.8 (structure known, semantics unknown) |
| 2 | Known vendor signature → declarative parser pack | vendor fields + explicit OCSF mapping | 0.85–1.0 |
| 3 | Field/type inference (typed tokens, key=value harvesting, timestamp grammar) | typed fields, observables | +0.05–0.15 on top of tier 1/4 |
| 4 | Drain3 template mining (per source) | template_id, params | 0.4–0.6 |
| 5 | Statistical similarity to known fingerprints | candidate parser + similarity score | 0.3–0.7 (proposal only, executed only if ≥ threshold) |
| 6 | ML service (optional): vendor classifier, anomaly scorer | label + probability; anomaly_score | additive; never blocks |
| 7 | Quarantine | raw + fingerprint + template + inferred fields | ≤ 0.3 |

Routing: the engine computes the fingerprint once, checks the **shape-hash routing cache** (`shape_hash → parser_id`), else runs all applicable `can_parse()` scorers, executes the best, then *always* runs tier 3 enrichment and, if `confidence < τ_known` (default 0.6), tier 4 → 5 → 6 → 7.

## 5. Drift subsystem

### 5.1 Parser drift (per `(source_id, parser_id)`)

Signals per window (default 500 events or 60 s):
1. Histogram of `shape_hash` (structural distribution).
2. Mean confidence; field-fill vector (fraction of events with each mapped field present).
3. Schema-validation pass rate; timestamp parse rate.

Detector: Jensen–Shannon divergence between the frozen **reference window** (captured at parser onboarding or last approval) and the current window; EWMA on confidence. Drift is declared when `JS > θ_js` for `k` consecutive windows **or** confidence EWMA falls by > δ, and *sustained* (not < 1 % rare deviations, which are labeled anomalies). This is the well-known statistical formulation (see `research.md` §5 — prior art; we implement, we do not claim).

On drift: emit `ParserDriftEvent` (with the two histograms as evidence), set parser health `degraded`, lower routing preference for that parser so tiers 4–5 get a vote, and invoke the **Parser Evolution** step.

### 5.2 Parser evolution (hypothesis H1)

```
drifted window ──► Drain3 templates T' ──► for each t' in T':
     align(typed_tokens(t'), typed_tokens(t_v1))  via Needleman–Wunsch,
     substitution cost = 0 if same token type, 1 if compatible, ∞ if incompatible
     ──► transfer field labels from v1 positions to aligned t' positions
     ──► synthesize declarative pack candidate P(v2) (YAML), score = aligned_labels / total_labels
──► write to unknown/parser_suggestions/<source>/<ts>.yaml  +  API /parsers/suggestions
──► admin: POST /parsers/{id}/promote  → validated, signed, hot-loaded
```

### 5.3 Resource drift

`drift/agent.py` loads `deployment/desired-state.yaml`, observes with `psutil` (CPU count, memory), Docker Engine API over the mounted socket (image digests, env), Kubernetes API when in-cluster, and the internal registries (parser versions, schema version, config hash). `DiffEngine` produces typed `DriftEvent`s with `desired`, `observed`, `severity`, `snapshot_hash`. Stored in the same event store with a provenance envelope whose `raw_event_id` is the hash of the observed snapshot (the snapshot itself is written to the raw store as the "raw" for forensic replay).

### 5.4 Correlator (hypothesis H2)

For each `ParserDriftEvent`, look back `W` (default 15 min) for `DriftEvent`s on the same tenant; attach `explained_by: [drift_event_id…]` ordered by temporal proximity. Never auto-remediates; produces evidence.

## 6. Storage

| Data | Adapter (default) | Alternatives | Notes |
|---|---|---|---|
| Raw | `LocalRawStore` (segment files) | `ObjectRawStore` (S3-compatible; interface defined, adapter in roadmap) | Append-only; gzip on rotate; index in SQL. |
| Normalized events, sources, parsers, fingerprints, unknown, drift, audit | `SQLStorage` via SQLAlchemy — SQLite (local/demo) **and** PostgreSQL (same code, dialect switch) | ClickHouse (roadmap: columnar analytics) | JSON column for the OCSF body + indexed columns (`tenant_id, source_id, ts, class_uid, tier, confidence, raw_event_id`). SQLite FTS5 index on `message` for search. |
| Vector | `NumpyVectorIndex` (shape n-gram MinHash + optional embedding), persisted `.npy` | pgvector / OpenSearch (roadmap) | Used only for unknown-cluster similarity and template similarity; raw logs are **not** vectorized. |
| Queue | Redis Streams | Kafka/NATS (roadmap) | `LOCAL` mode uses an in-process asyncio queue (single container demo). |

## 7. Scalability path

Single node (demo): `docker compose up` → collector + api + worker×2 + redis + ml (optional). All state on a volume.

Horizontal: N collectors (stateless) → Redis Streams (or Kafka) partitioned by `source_id` (ordering per source) → M workers in a consumer group → PostgreSQL. Drain3 state is per `source_id` and persisted to Redis so any worker can serve any source; the routing cache is shared in Redis. Hot data in SQL; cold raw in object storage. Numbers are measured, not promised — see `tests/performance/` and `docs/scalability.md`.

## 8. Multi-tenancy and security (summary; see `security.md`)

`tenant_id` is a column on every table and a prefix on every raw path. API keys map to tenants (`StaticKeyAuth`, env-configured; `NoAuth` only when `ULI_AUTH=none`). Parser packs are YAML (`safe_load`), executed by a declarative engine — no code loading from packs; regexes run through the `regex` module with a per-match timeout; line length capped (default 64 KiB; oversize is truncated *for parsing only*, raw is kept whole). Bundles are Ed25519-signed; the public key is baked into the image or mounted.

## 9. Validation of the architecture against requirements

| Requirement | How the architecture satisfies it |
|---|---|
| Unknown vendors | Ladder tiers 4–7 always produce an event; unknown registry + suggestions. |
| Parser drift | §5.1 detection, §5.2 evolution; confidence falls, fallback tiers vote. |
| Air-gapped | P5; `deployment/airgap/` builds a signed tarball with images, wheels, models, datasets, docs. |
| Scale | Queue + stateless workers + shared Drain3/routing state. |
| Provenance | Raw-first write, content hash, offset, parser/schema versions on every event. |
| Resource drift | §5.3; same store, same envelope. |
| Fail soft | P1; quarantine tier; per-event error list; metrics. |
