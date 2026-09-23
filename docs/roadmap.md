# Roadmap

Honest list of what is deliberately out of scope for this build, split by why: hackathon time
budget, an architectural interface that exists but has no adapter yet, or a design decision that
trades a capability for a simpler/safer system today. Each item cross-references the doc where the
gap is discussed in context — nothing here should be new information, this is the consolidated view.

## Security

- ~~Bundle signing~~ **Implemented**: Ed25519 sign/verify (`uli/security/signing.py`), wired into
  `POST /v1/parsers/bundles`, `ParserEngine.load_parsers_dir` (sibling `.sig` files), and the
  air-gap bundle script (`ULI_AIRGAP_SIGNING_KEY`). See `docs/security.md` §5,
  `docs/air-gapped-deployment.md` §3. Still opt-in (`ULI_BUNDLE_REQUIRE_SIGNATURE` defaults false),
  which is a deployment-config decision, not a missing capability.
- Auth beyond static API keys (OAuth/mTLS/SSO) — `docs/security.md` §2.
- TLS termination and rate limiting are assumed to be handled by a fronting proxy, not built in.

## Storage and scale

- **Object-storage raw backend** (`architecture.md` §6 lists it as an alternative to
  `LocalRawStore`): interface implied by `RawStore`'s usage, no S3-compatible adapter written.
- **ClickHouse adapter** for columnar analytics over normalized events (`architecture.md` §6):
  SQLite and PostgreSQL are the two implemented SQL backends; ClickHouse is not.
  **Kafka/NATS queue backend**: only Redis Streams and an in-process asyncio queue (`local` mode)
  exist; `architecture.md` §7 names Kafka/NATS as the horizontal-scale alternative.
- ~~Per-event SQL round-trip overhead~~ **Fixed**: `Pipeline.process_batch()` +
  `write_events_bulk`/`index_raw_bulk`/`upsert_sources_bulk` (`uli/storage/sql.py`) collapse the
  per-event `write_event`/`index_raw`/`upsert_source` calls into per-batch bulk statements, wired
  into the worker and local multi-line ingest. Measured ~2.6-2.7x throughput (185.6→500.7
  events/sec at n=1,000; 209.9→552.5 at n=100,000) and ~2.6-2.7x less CPU per event; ratio is
  consistent across runs, absolute events/sec varies with machine load (`docs/scalability.md` §0-1
  both show two separate runs). See `docs/scalability.md` §0. `record_unknown` stays per-event
  (fires only for low-confidence events, not the hot path).
- **1000-source scale target**: tested (`tests/performance/test_scale_sources.py`) — 5,000 events
  round-robin across 1,000 distinct source_ids at 494.6 events/sec, with per-source `event_count`
  verified exactly correct via `upsert_sources_bulk`'s aggregation. See `docs/scalability.md` §4b.
- **1M/10M-event benchmark runs**: still not executed — with batching, the *projection* (not a
  measurement) drops from ~94min/~16h to ~30min/~5h for 1M/10M respectively, still outside this
  session's time budget. The horizontal-scaling story (`architecture.md` §7 — N stateless workers
  behind Redis Streams, already demonstrated with 2 workers in `docker-compose.yml`) remains the
  intended answer to volume beyond a single process's now-higher floor.

## ML

- **Model persistence** (`docs/ml-strategy.md` §5): `AnomalyPool` models are in-memory only;
  `ml_models_dir` exists in config but nothing writes to it yet — a worker restart loses trained
  state.
- **Offline evaluation harness** for anomaly-score precision/recall against labeled data — scores
  are exposed for operator judgment today, not validated against ground truth.

## Detection / correlation

- **Cluster splitting**: `docs/unknown-source-detection.md` §6 — no way to split an
  unknown-vendor cluster that turns out to have merged two distinct formats via a `family_hash`
  collision, other than manual re-triage.
- **Kubernetes API probing** for resource drift (`docs/resource-drift.md` §5) — only `psutil` and
  the Docker Engine API are live; no in-cluster K8s adapter.

## Deployment

- Kubernetes manifests (`deployment/kubernetes/`) are minimal reference manifests, not
  production-hardened (no Ingress/TLS/HPA/NetworkPolicy) — see
  `deployment/kubernetes/README.md`.
- Air-gapped bundle+reload was verified by build and syntax-check, not by a full cycle on a
  genuinely offline second host (`docs/air-gapped-deployment.md` §4).

## Explicitly not planned (scope decisions, not gaps)

- Neural/LLM-based log parsing — evaluated and deliberately excluded (`research.md` §2.3,
  `docs/ml-strategy.md` §4): higher latency/cost and air-gap friction for a job the
  deterministic+statistical tiers already handle.
- Auto-remediation of resource/config drift — `architecture.md` §5.4 states the correlator "never
  auto-remediates; produces evidence" as a deliberate safety stance, not a missing feature.
