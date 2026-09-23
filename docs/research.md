# Research: Existing Technology for Universal Log Intelligence

**Status:** Phase 1 deliverable · **Date:** 2026-09-05 · **Audience:** engineers deciding what to build vs. reuse.

This document surveys the technologies a vendor-agnostic log normalization framework can stand on, evaluates each against the project's hard constraints (unknown vendors, parser drift, air-gapped operation, provenance, scale), and ends with an **Exact Research Gap** that separates what exists from what we are hypothesising.

Constraint key used in the tables:

| Column | Meaning |
|---|---|
| **Air-gap** | Can run with zero internet at runtime (no model download, no license ping, no cloud API). |
| **Unknown vendor** | What happens to a log whose format nobody has pre-configured. |
| **Drift** | What happens when a *known* vendor silently changes its format. |
| **Compute** | Typical CPU/RAM requirement at ingestion time. |

---

## 1. Log collection (transport layer)

| Tool | What it does | Architecture | Strengths | Weaknesses | Scale | Air-gap | Unknown vendor | Drift | Compute |
|---|---|---|---|---|---|---|---|---|---|
| **rsyslog / syslog-ng** | Receive/forward syslog (RFC 3164/5424, UDP/TCP/TLS, RELP). | Single daemon, rule-based routing. | Universal; every network device speaks syslog. | Parsing is regex/template-based per source; no schema concept. | Very high (100k+ EPS per node). | Yes | Passes raw line through | None | Tiny |
| **Fluent Bit** | Lightweight C collector: tail files, syslog, TCP, K8s metadata, Lua/regex parsers. | Plugin pipeline (input→parser→filter→output). | ~1–5 MB RSS/node; first choice at the edge. | Parsers are regex per source; failure = unparsed line. | High | Yes | Raw passthrough | None | Tiny |
| **Fluentd** | Ruby collector with the largest plugin catalog. | Same pipeline model. | Plugin breadth. | 50–100 MB RSS; slowest of the group (~¼ of Vector's EPS in public benchmarks). | Medium | Yes | Raw passthrough | None | Medium |
| **Vector (Datadog)** | Rust collector/aggregator with VRL transform language. | Sources→transforms→sinks; VRL compiled, must handle every error path. | Fastest per core; VRL forces error handling (fail-soft by construction). | Still per-source VRL programs written by humans. | Very high | Yes | Raw passthrough | None | Small |
| **Logstash** | JVM pipeline with Grok. | Input→filter→output, JRuby. | Grok pattern library is huge; mature. | Heavy (JDK 21), slow; Grok failures tag `_grokparsefailure` and leave message raw. | Medium | Yes | `_grokparsefailure` tag | None | Heavy |
| **OpenTelemetry Collector** | Vendor-neutral telemetry pipeline (logs/metrics/traces). `filelog` receiver with `json_parser`, `regex_parser`, `container` operators; OTTL for transforms. | Receivers→processors→exporters, Go. | Emerging standard; OTLP log data model (`body`, `attributes`, `resource`, `severity`) is a good *transport* envelope. | Log parsing operators are still hand-written per source. OTel explicitly recommends "reserve regex operators for legacy systems you cannot change". | High | Yes | Raw body passthrough | None | Small |
| **Kafka / Redpanda / NATS / Redis Streams** | Durable buffer between collectors and workers. | Partitioned log. | Decouples ingest from parsing; replay. | Ops overhead (Kafka); Redis Streams is simplest for single-node. | Very high | Yes | n/a | n/a | Varies |

**Conclusion:** Collection is a solved problem. We reuse it (syslog + HTTP + file tail, with a Redis Streams buffer) and spend zero innovation budget here.

---

## 2. Log parsing (structure extraction)

### 2.1 Deterministic / declarative parsers

| Tech | Notes |
|---|---|
| **Grok (Logstash), regex operators (OTel), VRL (Vector), props/transforms.conf (Splunk), KQL parsers (Sentinel ASIM)** | All are *human-written, per-source* extraction rules. Correct and fast for known sources; binary failure for unknown ones. Regex on untrusted input risks catastrophic backtracking (ReDoS) unless a linear-time engine (RE2/Hyperscan) or timeouts are used. |
| **Structural format parsers** (JSON, CEF, LEEF, syslog RFC5424 structured-data, logfmt, CSV, Apache/Nginx CLF) | Vendor-independent; a JSON or CEF parser needs no vendor knowledge. Cover a large fraction of modern security logs (firewalls emit CEF/LEEF/syslog+KV; cloud emits JSON). |

### 2.2 Template mining (unsupervised, statistic-based)

These algorithms take free-text lines and separate the constant "template" from the variable "parameters" **with no vendor knowledge**. Evaluated in the Loghub benchmark series (ICSE'19 tools paper; ISSTA'24 Loghub-2.0).

| Algorithm | Method | Online? | Strengths | Weaknesses | Compute |
|---|---|---|---|---|---|
| **Drain / Drain3** | Fixed-depth parse tree keyed on token count and leading tokens; similarity threshold to merge into clusters. | Yes (streaming) | Best overall accuracy/efficiency trade-off in every Loghub benchmark; linear in log size; parses 1 GB in tens of minutes on CPU. Drain3 (IBM, MIT license, PyPI 0.9.11) adds masking (IP/number/hex → wildcards), state persistence (file/Redis/Kafka), and snapshotting. | Sensitive to token-count changes (a drifted format with one extra field creates a *new* tree branch = new templates). Template-level accuracy (FGA) drops from 0.75 (Loghub-2k) to ~0.55 on the large-scale Loghub-2.0. Last PyPI release 2022 (stable, pure Python). | Very low |
| **IPLoM** | Iterative partitioning by token count, then token position, then bijective relationships. | Batch | Completes all Loghub-2.0 datasets; efficient. | Batch only; lower accuracy than Drain on diverse data. | Low |
| **Spell** | Longest-common-subsequence streaming. | Yes | Decent on HDFS. | LCS is quadratic in line length; poor at scale; failed 12-hour budget on Loghub-2.0. | Medium |
| **LenMa** | Length-vector clustering. | Yes | Simple. | Degrades sharply as template count grows (BGL/Android); failed 12-hour budget. | Medium |
| **AEL, Logram, LogCluster, LogSig, LFA, LogMine, Brain, Tipping (2024)** | Various frequency / n-gram / partition heuristics. | Mixed | Tipping reports higher accuracy than Drain on the LogPM benchmark. | Less mature tooling; less battle-tested. | Low–medium |

**Loghub-2.0 headline finding (ISSTA'24):** on large, imbalanced real datasets, *all* parsers degrade; **9 of 15 could not finish 14 datasets within 12 hours**; only Drain, IPLoM, LFA, LogCluster, LogSig, UniParser and LogPPT completed. Semantic (neural) parsers score higher per-message (PA/FTA) but lower on grouping and need GPUs.

### 2.3 Neural / LLM-based parsers (2022–2026)

| Method | Type | Notes |
|---|---|---|
| **UniParser (2022)** | Small neural token classifier trained across heterogeneous logs. | Good per-message accuracy; needs labeled data; GPU for training. |
| **LogPPT (2023)** | Prompt-tuned RoBERTa few-shot parser. | High FTA on small data; drops ~0.64→0.50 on Loghub-2.0; GPU. |
| **DivLog, LILAC (FSE'24), LogBatcher, LUNAR, OpenLogParser, SelfLog, LLM-TD, InferLog (2025)** | LLM in the loop, with adaptive parsing caches / clustering to minimise calls. | LILAC improves template F1 by ~70% over prior art; LogBatcher and LUNAR marginally better. **All depend on an LLM API or a multi-GB local model**; per-event LLM calls are unaffordable at ingestion rates; every design uses the LLM *offline* to build a cache/template set that a cheap matcher then serves online. |
| **Survey: "System Log Parsing with LLMs: A Review" (2025)** | — | Confirms the pattern: LLMs synthesize parsing artifacts; deterministic code executes them. |

**Conclusion:** Drain3 is the correct online, CPU-only, air-gap-safe template miner. LLM parsers are valuable only as *offline* artifact generators and are optional for an air-gapped deployment.

---

## 3. Log normalization and schema mapping

| Standard | Owner / status | What it is | Strengths | Weaknesses | Fit |
|---|---|---|---|---|---|
| **OCSF** | Linux Foundation (since Nov 2024); **v1.9.0 released 2026-08-03** (1.5 Apr 2025, 1.6 Aug 2025, 1.7 Nov 2025, 1.8 Mar 2026). 200+ orgs, AWS Security Lake native. | Vendor-neutral security event taxonomy: categories → classes (`class_uid`), activity IDs, typed objects (`src_endpoint`, `actor.user`, `device`), plus first-class `raw_data`, `unmapped`, `observables`, `metadata.product`, `metadata.version`. | Purpose-built for exactly this problem; extensible; JSON-schema-validated; has "unmapped" as a designed escape hatch. | Verbose; mapping effort per source; some classes still evolving. | **Chosen canonical schema.** |
| **ECS (Elastic)** | Elastic; converging into OTel semantic conventions. | Field naming standard. | Wide adoption in Elastic. | Elastic-centric; no activity/class taxonomy as rich as OCSF. | Provide as an *export mapping*, not the core. |
| **Splunk CIM** | Splunk. | Data-model add-on. | Mature. | Splunk-only. | Export mapping (future). |
| **Microsoft ASIM** | Microsoft Sentinel. | KQL parser functions. | Sentinel-native. | Sentinel-only. | Not targeted. |
| **CEF / LEEF** | ArcSight / IBM QRadar. | Key=value *transport* encodings inside syslog. | Trivial to parse; many firewalls/WAFs emit them. | Not a taxonomy; vendor extension keys unbounded. | Structural parser (Layer 1). |
| **Syslog RFC 3164 / 5424** | IETF. | Transport envelope (PRI, timestamp, host, app, procid, msgid, structured-data, msg). | Universal. | 3164 timestamps have no year/timezone; wild vendor deviations. | Structural parser (Layer 1). |
| **OTLP log record** | OpenTelemetry. | `time`, `severity`, `body`, `attributes`, `resource`, `trace_id`. | Neutral, good for pipelines. | Not a security taxonomy. | Accept as input format; carry `resource` into provenance. |
| **Sigma (spec v2, 2024–2026)** | SigmaHQ; pySigma, rsigma. | Vendor-neutral *detection* rule format with taxonomy + correlation rules. | Detection-side analog of OCSF; there is a pySigma OCSF pipeline effort. | Out of scope for normalization. | Roadmap: run Sigma over normalized events. |

**Conclusion:** Do not invent a schema. Use OCSF 1.9 as the canonical body and wrap it in a thin **provenance envelope** (our addition) that OCSF does not fully specify (raw offsets, hashes, parser lineage, confidence, tier).

---

## 4. Unknown-log detection and vendor identification

| Approach | Where it exists | Notes |
|---|---|---|
| **Signature matching** (source-type detection in Splunk, `sourcetype` auto-detection; Elastic integration auto-detect; Cribl "event breakers") | Commercial SIEM/pipelines. | Regex/prefix signatures decide which parser to run. Unknown = falls to a default type; stays raw. |
| **Heterogeneous log clustering** (UHAD; "System log clustering approaches for cyber security" survey, 2020; layout+content hierarchical clustering) | Academic. | Cluster events by token layout to group unknown formats; used for filtering and anomaly detection, not for automatic parser generation. |
| **Structural / layout fingerprints** | Appears implicitly in Drain's token-count keying, in Cribl event breakers, and in the Cisco/Splunk drift patent's "format representation → hash". | Reducing a line to a **token-class shape** (e.g. `TS HOST WORD[NUM]: WORD=IP WORD=NUM`) and hashing it is a known primitive; we found no open-source system that uses it as the *routing key* across all seven layers of a parsing ladder. |
| **Quarantine + learning loop** | Databahn (commercial, 2025): quarantines unparseable events, agentic AI drafts parser + OCSF mapper for engineer approval. Axoflow: auto-updating parsers. | Commercial, cloud-hosted AI. Mechanism undisclosed. |

---

## 5. Parser-drift / format-drift detection

| Approach | Source | Mechanism | Notes |
|---|---|---|---|
| **US 12373324 B1 "System and method for format drift and format anomaly detection"** (Cisco Technology; formerly Splunk; priority 2021-12-03, granted 2025-07-29) | Patent | Extract a *format representation* of each field, one-way hash it, keep counts per hash, build a probability distribution, compare a reference window to the current window with a distance function; divergence above a statistical threshold = **drift**; rare deviations (<1%) = **anomaly**; reference window resizes on change-points; extraction rules may be regenerated. | This is the canonical statistical formulation. **It is prior art; we do not claim it.** The same idea appears in generic ML data-drift tooling (PSI, KS, Jensen–Shannon over categorical histograms). |
| **Schema drift monitoring** (Databahn, Cribl roadmap, Axoflow) | Commercial | Field appears/disappears/changes type vs expected source schema → flag, quarantine, alert. | Confirms the problem is real and unsolved in open source. |
| **Anomaly detection on "unstable logs"** ("LLM meets ML: Data-efficient Anomaly Detection on Unstable Logs", 2024) | Academic | Studies logs whose templates evolve; goal is anomaly detection robustness, not parser repair. | Adjacent, not the same. |
| **Open-source pipelines (Vector/Fluent Bit/OTel/Logstash)** | OSS | **None** ship parser-health or drift metrics beyond "parse failed" counters. | The open-source gap. |

---

## 6. Log anomaly detection (ML)

| Method | Type | Compute | Air-gap | Verdict for ingestion path |
|---|---|---|---|---|
| **DeepLog (2017)** | LSTM over template-ID sequences. | GPU to train, CPU OK to infer. | Yes if bundled | Sequence model; brittle to template drift (new template = "anomaly"). |
| **LogAnomaly (2019)** | Template semantics + count vectors. | Medium | Yes | Better robustness on unstructured data. |
| **LogBERT / LAnoBERT / LogGPT (2021–2023)** | Masked-LM transformers over template sequences. | Heavy (GPU) | Only with a bundled model | SOTA on benchmarks; too heavy for the primary container; struggles on short sequences. |
| **Classical: Isolation Forest, One-class SVM, PCA (Xu et al. 2009)** | Count/feature vectors per window. | Tiny (scikit-learn) | Yes | Cheap, explainable, good first line; recommended as the *optional* online scorer. |
| **Comprehensive studies (2023–2025, e.g. PMC12185583, arXiv 2307.16714)** | Surveys | — | — | Consistent message: semi-supervised deep models win on benchmarks; classical models are competitive in practice and far cheaper; **all** degrade under template drift → drift detection is a prerequisite for anomaly detection, not an add-on. |

---

## 7. Resource / configuration drift detection

| Tool | Scope | Mechanism | Notes |
|---|---|---|---|
| **Argo CD / Flux** | Kubernetes | Continuous reconciliation: structured diff of rendered Git manifests vs live API objects; self-heal on auto-sync. | Gold standard for *desired vs observed* in K8s. |
| **driftctl** (Snyk, community-maintained) | Terraform/cloud | Compare state file vs cloud API. | Slowed development. |
| **KubeDiff, Kyverno, OPA Gatekeeper** | K8s | Policy/diff. | |
| **Firefly, Safeguard, commercial** | Multi-cloud | Same pattern. | |

**Conclusion:** the *Desired → Observed → Diff → Event* pattern is well established. What we found nowhere: storing drift events with the **same provenance model as logs** and **time-correlating infra drift with parser drift** ("the parser broke 90 s after the container image digest changed").

---

## 8. Real-world log data sources (for development, tests and demo)

| Source | License | Format / vendor | Acquisition |
|---|---|---|---|
| **Loghub** (logpai; Zenodo 3227177) — Linux, Apache, OpenSSH, Mac, HealthApp, Proxifier, Zookeeper, Hadoop, HDFS, BGL, Thunderbird, Windows, Android, OpenStack, Spark, HPC | Free for research/academic with citation (Zhu et al., ISSRE'23). Loghub-2.0 (Zenodo 8275861) under CC-BY. | Syslog-like OS logs, Apache error log, SSH auth, macOS unified log, Android logcat, HDFS/Hadoop app logs. | `scripts/fetch_logs.py` downloads the small ones (Linux 25k lines, Apache 52k, OpenSSH 655k, Mac 117k, HealthApp 253k, Proxifier 21k, Zookeeper 74k) with SHA-256 verification. |
| **SecRepo** (secrepo.com) | Mixed, per-file (mostly permissive/CC). | Zeek (Bro) conn/dns/http logs, Squid proxy access logs, auth.log, Windows. | `fetch_logs.py` with per-file license note. |
| **This host** (`journalctl -o json`, `/var/log/syslog`, `docker logs`) | Owner's own data. | systemd journal JSON, syslog, container stdout. | Live tail via collector; used in demo Scenario 1 and the air-gap scenario. |
| **Wikimedia EventStreams** (`stream.wikimedia.org/v2/stream/recentchange`) | CC0 metadata; no auth. | JSON over SSE, real-time. | Live structured-JSON source for "unknown vendor" JSON demo; internet only, so used in *online* dev only. |
| **Synthetic vendor variants** (`scripts/synth_vendor.py`) | Ours | `vendor_v1`, `vendor_v2`, `vendor_v3`, `unknown_variant` generated by controlled mutation of real lines (reorder, add, remove fields; change delimiters). | Deterministic (seeded) — used **only** for drift/adversarial tests, never presented as real. |

---

## 9. Exact Research Gap

The following statements are what the evidence above supports.

### 9.1 What exists (KNOWN — reuse, do not reinvent)

1. **Collection** (syslog, file tail, OTLP, Kafka/Redis buffering) — commodity.
2. **Structural parsers** for JSON, CEF, LEEF, syslog, logfmt, CLF — commodity.
3. **Online template mining** — Drain3 is the practical CPU choice; accuracy limits at scale are quantified (FGA ≈ 0.55 on Loghub-2.0).
4. **Canonical security schema** — OCSF 1.9 with `raw_data`/`unmapped`/`observables`.
5. **Statistical format-drift detection** via hashed format histograms and divergence — described in the Cisco/Splunk patent and generic data-drift tooling.
6. **Quarantine → AI-drafted parser → human approval** workflow — shipped commercially (Databahn), cloud-hosted, mechanism undisclosed.
7. **Desired-vs-observed resource drift** — Argo CD/Flux/driftctl.
8. **Log anomaly detection** — classical (Isolation Forest/PCA) and deep (DeepLog/LogBERT), with known fragility under drift.
9. **Provenance fields** in OCSF (`raw_data`, `metadata.log_provider`, `metadata.original_time`) — but no standardized *byte-offset / hash / parser-lineage* model.

### 9.2 What exists only as a combination we have not found assembled in open source (COMBINATION)

10. A **single confidence-scored, multi-layer parsing ladder** (structural → signature → field inference → template mining → similarity → optional ML → quarantine) where **every layer is a total function** (always returns an event, never raises), so ingestion cannot fail on any input. Individual layers are known; the guaranteed-degradation ladder with per-tier confidence is not something Vector/Fluent Bit/OTel/Logstash provide.
11. **Structural token-class fingerprints used as the routing key** for parser selection, drift histograms, unknown clustering and similarity search — the primitive is known; using one representation for all four is an engineering combination.
12. **Provenance envelope** (content hash + segment offset + parser id/version + schema version + tier + confidence) attached to every OCSF event, enabling byte-exact forensic reconstruction — combination of OCSF + content-addressed raw store.
13. **Resource-drift events stored under the same provenance model as log events** in the same store.
14. **Air-gapped, signed parser-bundle hot-loading** — signed artifacts (cosign/minisign style) + plugin registries both exist; applying them to parser packs with compatibility checks and hot reload without rebuild is a combination.

### 9.3 Hypotheses requiring validation (NOVEL HYPOTHESIS)

H1. **LLM-free parser evolution by semantic label transfer.** When drift is detected on source S under parser P(v1), mine templates from the drifted window with Drain3, align each new template's *typed-token sequence* to P(v1)'s known template(s) using sequence alignment (Needleman–Wunsch with type-aware substitution costs), and **transfer the v1 field labels to the aligned v2 positions** to synthesize a candidate declarative parser P(v2) with a computed confidence, entirely offline on CPU. The commercial equivalent uses an LLM in the cloud. *Validation:* on controlled `vendor_v1→v2→v3` mutations (field add/remove/reorder/delimiter change), measure fraction of fields correctly re-mapped without human edits.

H2. **Cross-domain drift correlation.** Time-join parser-drift events with resource-drift events (image digest, env var, config hash, parser/schema version) within a window, and attach an `explained_by` link when a resource change precedes a parser-confidence drop. We found no system that correlates infrastructure drift with log-format drift. *Validation:* inject an image/config change followed by a format change in the demo; verify the link is produced and no false link is produced for uncorrelated changes.

**Validated**: `tests/integration/test_drift_correlation.py` runs exactly this protocol end-to-end
through the real pipeline (not the correlator function in isolation) — a resource-drift event
followed by a genuine parser-drift event (structurally different JSON under the same
`structural.json` parser) — and asserts the link is produced, plus a negative control (zero-width
correlation window) asserting no false link. Writing this test surfaced two real bugs that had
made `explained_by` silently empty in every prior run: `uli/pipeline.py` was calling
`storage.write_parser_drift()` a second time on an event `DriftMonitor.observe()` had already
persisted, raising a primary-key `IntegrityError` that the broad `except Exception` around
correlation swallowed every time; and `uli/drift/correlator.py` compared a timezone-aware
in-memory datetime against a timezone-naive one read back from SQLite (a known SQLAlchemy/SQLite
round-trip gotcha), which raised `TypeError` before the fix landed. Both fixed; H2 is now
implemented, exercised, and passing, not just a validated-on-paper hypothesis.

H3. **Shape-fingerprint routing cache improves throughput without accuracy loss.** Caching `shape_hash → parser_id` should let the hot path skip signature scanning for the overwhelming majority of events (logs are Zipfian in template frequency). *Validation:* benchmark events/sec with cache on/off on Loghub data; report cache hit rate.

H4. **Drift-aware anomaly scoring.** Suppressing anomaly scores during a confirmed parser-drift window (rather than letting drift masquerade as anomalies) reduces false positives. Motivated by the survey finding that all sequence models flag new templates as anomalies. *Validation:* measure anomaly-flag rate during injected drift with and without suppression.

### 9.4 What we explicitly do **not** claim

- We do not claim format-drift detection is new (patented prior art, §5).
- We do not claim quarantine-and-suggest workflows are new (Databahn, §4).
- We do not claim a new template-mining algorithm; we use Drain3 and cite its measured limits.
- We do not claim a new schema; we use OCSF 1.9.

---

## Sources

- Loghub: https://zenodo.org/records/3227177 · Loghub-2.0: https://zenodo.org/records/8275861 · https://github.com/logpai/loghub-2.0
- Loghub-2.0 paper (ISSTA'24): https://arxiv.org/abs/2308.10828 · Tools & Benchmarks (ICSE'19): https://arxiv.org/pdf/1811.03509
- Drain3: https://github.com/logpai/Drain3 · https://pypi.org/project/drain3/ · Go port: https://github.com/kloudmate/drain3
- Tipping (2024): https://arxiv.org/pdf/2408.00645 · Logram: https://arxiv.org/pdf/2001.03038
- LLM parsing: LILAC https://github.com/logpai/LILAC · LUNAR https://arxiv.org/pdf/2406.07174 · Review https://arxiv.org/html/2504.04877v2 · Survey https://arxiv.org/pdf/2502.00677 · InferLog https://arxiv.org/pdf/2507.08523 · UniParser https://arxiv.org/pdf/2202.06569
- OCSF: https://github.com/ocsf/ocsf-schema/releases · https://ocsf.io/ · https://www.linuxfoundation.org/press/open-cybersecurity-schema-framework-ocsf-joins-the-linux-foundation-to-optimize-critical-security-data
- OpenTelemetry logs: https://opentelemetry.io/blog/2024/otel-collector-container-log-parser/ · https://signoz.io/blog/parsing-logs-with-the-opentelemetry-collector/
- Collectors: https://onidel.com/blog/log-shipping-benchmark-2025 · https://devopsboys.com/blog/fluent-bit-vs-fluentd-vs-vector-log-collectors-2026 · https://nxlog.co/news-and-blog/posts/logstash-alternatives-and-competitors
- Drift patent: https://patents.google.com/patent/US12373324B1/en · Databahn schema drift: https://www.databahn.ai/blog/maintaining-99-ocsf-compliance-at-enterprise-scale-the-schema-drift-challenge · Axoflow: https://axoflow.com/axoflow-platform-vs-cribl
- Anomaly detection: https://pmc.ncbi.nlm.nih.gov/articles/PMC12185583/ · https://arxiv.org/pdf/2307.16714 · LogBERT https://github.com/HelenGuohx/logbert · LAnoBERT https://arxiv.org/pdf/2111.09564 · LogGPT https://arxiv.org/pdf/2309.14482 · Unstable logs https://arxiv.org/pdf/2406.07467
- Resource drift: https://oneuptime.com/blog/post/2026-03-13-flux-cd-vs-argocd-drift-detection/view · https://komodor.com/blog/drift-detection-in-kubernetes/ · https://safeguard.sh/resources/blog/best-infrastructure-drift-detection-tools
- Sigma v2: https://blog.sigmahq.io/introducing-sigma-specification-v2-0-25f81a926ff0 · https://github.com/timescale/rsigma
- Datasets: https://github.com/shramos/Awesome-Cybersecurity-Datasets · https://github.com/neu5ron/TMInfosec/blob/master/Datasets/Log_Records.md · https://wikitech.wikimedia.org/wiki/Event_Platform/EventStreams · https://www.sciencedirect.com/science/article/pii/S0167404820300250
