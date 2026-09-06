# Roadmap

Honest list of what is deliberately out of scope for this build, split by why: hackathon time
budget, an architectural interface that exists but has no adapter yet, or a design decision that
trades a capability for a simpler/safer system today. Each item cross-references the doc where the
gap is discussed in context — nothing here should be new information, this is the consolidated view.

## Security

- **Bundle signing** (`docs/security.md` §5, `docs/air-gapped-deployment.md` §3): config fields
  exist (`bundle_pubkey_path`, `bundle_require_signature`); no Ed25519 signing/verification code is
  implemented. `POST /v1/parsers/bundles` currently trusts any authenticated caller's YAML.
- Auth beyond static API keys (OAuth/mTLS/SSO) — `docs/security.md` §2.
- TLS termination and rate limiting are assumed to be handled by a fronting proxy, not built in.

## Storage and scale

- **Object-storage raw backend** (`architecture.md` §6 lists it as an alternative to
  `LocalRawStore`): interface implied by `RawStore`'s usage, no S3-compatible adapter written.
- **ClickHouse adapter** for columnar analytics over normalized events (`architecture.md` §6):
  SQLite and PostgreSQL are the two implemented SQL backends; ClickHouse is not.
  **Kafka/NATS queue backend**: only Redis Streams and an in-process asyncio queue (`local` mode)
  exist; `architecture.md` §7 names Kafka/NATS as the horizontal-scale alternative.
- **Per-event SQL round-trip overhead**: profiling during this build (`cProfile` over 2,000
  `pipeline.process()` calls) showed `write_event`/`index_raw`/`upsert_source`/`record_unknown`
  issuing roughly 9-10 individual SQL `execute()` calls per event, and SQLAlchemy Core's
  per-statement machinery (cache-key generation, type coercion) dominating CPU time over the
  parsing ladder itself. This is consistent with the measured ~210 events/sec single-process
  throughput (`docs/scalability.md`). Batching per-event writes into fewer statements (or a
  bulk-insert path for the hot fields) is the highest-leverage next optimization, and was
  identified but **not implemented** in this build — the honest tradeoff made was to spend the
  remaining time budget on documentation, deployment verification, and demo completeness rather
  than a storage-layer rewrite this late in the build. See `docs/scalability.md` §3.
- **1M/10M-event benchmark runs**: not executed (`tests/performance/test_throughput.py`'s own
  docstring explains why — at the measured single-process rate this would take on the order of an
  hour+, outside this benchmark's time budget). The horizontal-scaling story
  (`architecture.md` §7 — N stateless workers behind Redis Streams, already demonstrated with 2
  workers in `docker-compose.yml`) is the intended answer to volume, not a faster single process.

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
