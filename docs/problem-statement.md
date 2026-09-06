# Problem Statement

## The one-sentence version

Every production log pipeline can normalize a log **only after a human has written a parser for that exact format**; when an unknown vendor, an unknown version, or a silently changed format arrives, the pipeline either drops the event, stores it as useless raw text, or — worst — keeps running a stale parser and emits silently corrupted "normalized" data.

## Why this matters

Security and operations teams consume logs from dozens to thousands of heterogeneous sources: firewalls, IDS/IPS, VPNs, proxies, WAFs, Linux/Windows hosts, containers, Kubernetes, applications, cloud services. Detection rules, dashboards, ML models and forensic investigations all assume the data is in one consistent, typed schema. In practice:

| Failure | Consequence |
|---|---|
| Unknown vendor onboarded | Days of parser engineering before the first useful event; until then the SOC is blind to that source. |
| Vendor firmware update changes one field | Existing regex keeps "matching" but fields shift; dashboards go quiet or lie; nobody is alerted. |
| Malformed / hostile input | A regex catastrophically backtracks or a parser raises; one bad line stalls a worker, or the event is dropped. |
| Raw log discarded after parsing | Forensic reconstruction impossible; compliance failure. |
| Air-gapped site | Cloud-parsing/AI products (the only ones that automate the above) cannot be deployed at all. |

## What "solved" looks like (acceptance conditions)

1. **Total ingestion.** For any byte sequence presented as a log event, the system produces exactly one stored raw record and exactly one normalized event (possibly low-confidence, possibly quarantined) — never an exception, never a drop.
2. **Graded quality, not binary.** Each normalized event carries a `tier` (which layer produced it) and a `confidence` ∈ [0,1], so consumers can filter by trust instead of guessing.
3. **Traceability.** Every normalized event resolves back to the exact raw bytes (hash + store offset), the parser id/version that produced it, and the schema version it conforms to.
4. **Unknown ≠ lost.** Unknown formats are fingerprinted, template-mined, field-inferred, clustered, and turned into a *draft parser pack* an administrator can approve — with no external AI service.
5. **Drift is detected, not suffered.** A structurally different stream under a known parser raises a drift event with evidence, lowers confidence, re-routes to fallback layers, and proposes a successor parser.
6. **Plug-and-play vendors.** A new vendor is a signed bundle dropped into a directory or POSTed to an API; the running system validates, checks compatibility, and hot-loads it. No rebuild, no restart.
7. **Air-gapped by construction.** Zero runtime network dependencies; everything (images, packages, models, datasets, docs) arrives as a signed offline bundle.
8. **Observable.** The pipeline emits metrics on its own health: parse tiers, confidence, drift, queue depth, latency, ML availability.
9. **Infrastructure drift is first-class.** Desired vs observed resources (CPU, memory, image digests, config, parser/schema versions) are diffed and stored as events with the same provenance guarantees, and correlated with parser drift.

## Scope for this iteration

**In:** syslog (UDP/TCP), HTTP ingest, file tail, container stdout; JSON/CEF/LEEF/syslog/logfmt/CLF structural parsers; declarative vendor parser packs; Drain3 template mining; fingerprint-based unknown engine; statistical drift detection; label-transfer parser suggestion; OCSF 1.9 canonical schema with provenance envelope; SQLite (local) and PostgreSQL storage adapters; Redis Streams queue with in-process fallback; optional ML sidecar (vendor classifier + Isolation Forest scorer); resource drift diff engine; FastAPI API; Prometheus metrics; Docker Compose; air-gap bundle scripts; minimal Kubernetes manifests; unit/integration/e2e/adversarial/performance tests.

**Out (roadmap):** Kafka adapter, ClickHouse adapter, Windows EVTX binary parsing, Sigma rule evaluation, UI beyond API docs, multi-node HA.

## Non-goals and honesty constraints

- We are not inventing a template-mining algorithm or a schema.
- We do not claim statistical drift detection or quarantine-and-suggest workflows as our invention (see `research.md` §9.4).
- Benchmarks are measured on the documented hardware; nothing is extrapolated.
