# Universal Log Intelligence (ULI)

Vendor-agnostic log ingestion, parsing, and normalization to OCSF 1.9, with an
unknown-source learning engine, parser-drift resilience, and resource-drift
detection. Designed to run fully air-gapped.

See [`docs/final-report.md`](docs/final-report.md) for the audit trail: requirements
validated against the running system, real bugs found and fixed during the build,
measured performance, and an honest list of what was deliberately left undone.

## 1. The problem

Every production log pipeline can normalize a log **only after a human has
written a parser for that exact format**. When an unknown vendor, an unknown
version, or a silently changed format arrives, the pipeline drops the event,
stores it as useless raw text, or — worst — keeps running a stale parser and
emits silently corrupted "normalized" data. See [`docs/problem-statement.md`](docs/problem-statement.md)
for the full acceptance criteria this project is held to.

## 2. Existing approaches and the gap

CIM (Splunk), ECS (Elastic), ASIM (Microsoft), and OCSF are production-grade
**normalized schemas** — they define the target shape, not how to get there
from an unknown or drifting source. Commercial log pipelines (Databahn,
Cribl) and a Cisco/Splunk patent (US 12373324B1) already do quarantine +
structural-drift detection, some with an LLM in the loop. [`docs/research.md`](docs/research.md)
documents this survey in full and classifies every technique we use as
**KNOWN**, **COMBINATION**, or **NOVEL HYPOTHESIS** (§9) — we do not claim
prior art as our own. The actual, defensible gap we target: an **LLM-free,
air-gappable** pipeline that (a) never fails closed on unknown/drifting input,
and (b) can *evolve* a parser for a drifted format by aligning old and new
token sequences (Needleman–Wunsch label transfer, hypothesis H1) instead of
calling out to a cloud model.

## 3. Architecture

```
collector(s) → HTTP ingest / API → queue (Redis Streams | in-proc) → worker(s)
   → 7-tier parsing ladder → OCSF normalization → storage (SQLite/Postgres)
                                                 → raw store (content-addressed)
```

Parsing ladder (every tier is a total function — never raises):

| Tier | Layer | Confidence |
|---|---|---|
| 1 | Structural (JSON, CEF, LEEF, syslog 5424/3164, logfmt, CLF, log4j) | 0.6–0.8 |
| 2 | Declarative vendor pack (signature match) | 0.85–1.0 |
| 3 | Field/type inference (always runs, enriches 1/4) | +0.05–0.15 |
| 4 | Drain3 template mining | 0.4–0.6 |
| 5 | Shape-similarity to known fingerprints | 0.3–0.7 |
| 6 | Optional ML sidecar (annotate-only) | additive |
| 7 | Quarantine (raw + fingerprint + template preserved) | ≤0.3 |

Full design: [`docs/architecture.md`](docs/architecture.md), [`docs/parser-design.md`](docs/parser-design.md),
[`docs/data-model.md`](docs/data-model.md).

## 4. Quickstart (local, no Docker)

```bash
git clone <this repo> && cd universal-log-intelligence
python3 -m venv .venv && .venv/bin/pip install -e ".[ml,dev]"
.venv/bin/python scripts/fetch_logs.py        # pulls real sample datasets (Loghub/SecRepo)
ULI_MODE=local .venv/bin/python -m uvicorn uli.api.app:app --port 8080 &
curl -X POST localhost:8080/v1/ingest -H 'content-type: application/json' \
  -d '{"source_id":"demo:1","lines":["Jun 14 15:16:01 web1 sshd[1234]: Failed password for root from 1.2.3.4 port 4444 ssh2"]}'
curl localhost:8080/v1/events | head
```

## 5. Docker deployment

```bash
docker compose up --build              # api + 2 workers + redis
docker compose --profile ml up --build       # + ML sidecar
docker compose --profile collector up --build # + Go syslog/file collector
```

Services: `api` (:8080), `worker` (x2, no exposed port), `redis` (:6379),
optional `ml` (:8090), optional `collector` (:5514 udp/tcp). All state lives
in the `uli-data` named volume. Measured image sizes and resource use are in
[`docs/ml-strategy.md`](docs/ml-strategy.md) and [`docs/scalability.md`](docs/scalability.md)
(measured on this machine, not fabricated).

Minimal, non-production-hardened Kubernetes manifests for the same images are in
[`deployment/kubernetes/`](deployment/kubernetes/README.md) (`kubectl apply -f
deployment/kubernetes/`), with an explicit note there about the SQLite/PostgreSQL
storage tradeoff at multi-node scale.

## 6. Air-gapped deployment

`deployment/airgap/build_bundle.sh` produces one checksummed tarball containing
the built Docker images (`docker save`), vendor parser packs, schemas, sample
datasets, and docs, plus a `LOAD.sh` that `docker load`s everything and brings
the stack up with no network access. See
[`docs/air-gapped-deployment.md`](docs/air-gapped-deployment.md) for the full
procedure — including an explicit note that the bundle is integrity-checked
(SHA-256), **not yet code-signed** (tracked in `docs/security.md` §5 and
`docs/roadmap.md`). No service in this repo makes an outbound network call at
runtime.

## 7. Adding a new (known) vendor

Drop a declarative YAML pack into `parsers/vendors/` (or `POST /v1/parsers/bundles`
with the pack YAML — no signature verification exists yet, see `docs/security.md` §5)
— no code, no rebuild, no restart. Format and worked
example: [`docs/parser-design.md`](docs/parser-design.md) §4. Existing examples:
`parsers/vendors/{pfsense_filterlog,squid_access,zeek_conn}.yaml`.

## 8. Unknown-vendor workflow

1. A never-configured format falls through tiers 1–3 into Drain3 (tier 4) and
   is fingerprinted (structural shape + family hash).
2. Events sharing a family hash accumulate in an **unknown cluster**
   (`GET /v1/unknown`) with merged inferred fields and mined templates.
3. Once a cluster passes `unknown_suggest_min_events`, `POST /v1/unknown/{id}/suggest`
   synthesizes a draft declarative pack (same alignment machinery as H1 parser
   evolution, run against nothing instead of a prior version).
4. An admin reviews and `POST /v1/parsers/suggestions/{id}/promote`s it —
   the pack is validated, hot-loaded, and starts serving as tier 2 immediately.

Details: [`docs/unknown-source-detection.md`](docs/unknown-source-detection.md).

## 9. Parser-drift resilience

If a known source's structural-shape histogram diverges (Jensen–Shannon) from
its reference window, confidence on that parser degrades, fallback tiers take
over so **no data is lost**, and (hypothesis H1) the engine attempts to align
the old parser's labeled positions against the new Drain3 template via
Needleman–Wunsch and synthesize a v2 pack candidate, which runs in **shadow
mode** until promoted. Example: `timestamp host process[pid]: message` drifting
to `timestamp host process(pid): severity message` is handled without an
outage. See [`docs/parser-design.md`](docs/parser-design.md) §7.

## 10. Resource/config drift

`uli.drift.resource` diffs `deployment/desired-state.yaml` against observed
CPU/memory (psutil) and container state (Docker Engine API), writing
`DriftEvent`s into the *same* store with the *same* provenance guarantees as
log events (the observed snapshot itself is content-addressed in the raw
store). `docs/resource-drift.md` has the schema and the H2 correlation with
parser drift.

## 11. Running the tests

```bash
.venv/bin/pytest tests/unit -q                 # 43 tests, pure logic
.venv/bin/pytest tests/integration -q          # 20 tests (parametrized), real sample datasets, full pipeline + HTTP layer
.venv/bin/pytest tests/adversarial -q          # 21 tests (parametrized), hostile/malformed/ReDoS inputs
.venv/bin/pytest tests/e2e -m e2e -q           # 8 tests, against a running docker compose stack
.venv/bin/pytest tests/performance -m performance -s   # measured throughput/latency
make test        # unit+integration+adversarial, excludes performance/e2e
```

`docs/testing.md` documents what each layer actually asserts (never "it ran
without crashing" alone — every adversarial case also checks tier/confidence
bounds and a wall-clock ceiling as a ReDoS guard).

## 12. Performance testing

`tests/performance/` measures real events/sec, CPU, RAM, and p50/p95/p99
latency on the host it runs on; numbers are written to `docs/scalability.md`
verbatim from the last run, never hand-typed. Re-run with `make test-performance`
before citing a number from that doc.

## 13. Demo script

With the stack up (`docker compose up -d` or `make run`):

```bash
.venv/bin/python scripts/demo.py     # or: make demo
```

Runs and narrates all six required scenarios end to end against the live API: known
vendor (real OpenSSH log line), parser-drift resilience, unknown vendor →
fingerprint → cluster → suggest → promote, resource drift, air-gapped operation
(no outbound network call needed), and raw provenance round-trip. Each step
asserts and prints its own result — see `scripts/demo.py` for details.

## Repository layout

```
uli/            core Python package (parsers, engine, pipeline, drift, storage, api, ml)
parsers/vendors/  declarative YAML parser packs
collector/      Go syslog/file collector (dumb forwarder, no parsing)
deployment/     Dockerfiles, compose, air-gap bundler, k8s manifests
docs/           architecture, research, and operational docs
tests/          unit, integration, adversarial, e2e, performance
scripts/        fetch_logs.py (real datasets), demo.py (6-scenario demo)
```

## License

MIT (see `LICENSE`). Sample log datasets under `logs/samples/` retain their
original third-party licenses — see the `LICENSE-NOTE.txt` in each dataset
directory.
