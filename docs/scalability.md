# Scalability

Real, measured numbers from `tests/performance/test_throughput.py`, persisted verbatim in
`tests/performance/last_run_results.json` and copied here by hand — this file states plainly what
was measured, on what hardware, and what was deliberately not attempted, rather than extrapolating.

## 1. Measured throughput and latency (single process, SQLite, no batching)

Machine: 10 logical CPUs, Python 3.12, in-process pipeline (raw store → 7-tier ladder → normalize
→ SQLite write), single thread, no batching — i.e. the *floor* configuration, not the scaled
deployment.

| n (events) | events/sec | p50 (ms) | p95 (ms) | p99 (ms) | max (ms) | wall (s) | CPU user (s) | CPU sys (s) | RSS Δ (MB) |
|---|---|---|---|---|---|---|---|---|---|
| 1,000 | 216.0 | 4.33 | 5.58 | 6.38 | 15.84 | 4.63 | 4.49 | 0.14 | 3.6 |
| 10,000 | 209.9 | 4.45 | 5.79 | 6.63 | 17.08 | 47.64 | 46.37 | 1.25 | 1.1 |
| 100,000 | 176.7 | 5.31 | 8.35 | 10.82 | 37.42 | 565.85 | 547.72 | 15.67 | 8.4 |

Read honestly, not favorably: throughput **degrades** as n grows (216 → 210 → 177 events/sec) and
every latency percentile **grows** (p50 4.3ms → 5.3ms; p99 6.4ms → 10.8ms). This is consistent with
a SQLite table that keeps growing across the run — more rows means slower index maintenance per
write, at these scales. Memory stays flat (RSS delta is single-digit MB even at 100K events — no
leak), and CPU user time tracks wall time almost 1:1 at every scale, meaning this workload is
**CPU-bound, not I/O-bound**: SQLite fsync/disk time is not the bottleneck.

## 2. Where the CPU time actually goes

A `cProfile` run over 2,000 `pipeline.process()` calls (ad hoc, during this build, not a checked-in
benchmark) attributed the majority of cumulative time to SQLAlchemy Core's per-statement machinery
— cache-key generation, type coercion (`sql/coercions.py:expect`, 215,190 calls), and statement
compilation — rather than to the parsing ladder itself (`engine.py:run` was 3.0s of an 18.2s
total). The proximate cause: a single processed event triggers roughly 9-10 separate SQL
`execute()` calls spread across `write_event`, `index_raw`, `upsert_source`, and `record_unknown` —
19,000 `execute()` calls for 2,000 events. **This was identified but not fixed in this build** —
see `docs/roadmap.md` for why (time-budget tradeoff, documented rather than silently deferred) and
what the fix would look like (batching per-event writes into fewer statements, or a bulk-insert
path).

## 3. Why 1M/10M were not run

At the measured 100K rate (~177 events/sec, and degrading), 1M events would take on the order of
94 minutes and 10M on the order of 16 hours in this exact single-process, unbatched configuration —
outside this benchmark's time budget. `tests/performance/test_throughput.py`'s own docstring states
this explicitly. Extrapolating a "projected" number for 1M/10M and presenting it as measured would
violate the project's non-fabrication policy for performance numbers (`docs/testing.md` §3); this
section states the *reasoning* for why they weren't run instead.

## 4. The actual answer to "how does this scale": horizontal, not a faster single process

Per `architecture.md` §7, the intended scaling path is **not** a single Python process going
faster — it's N stateless collectors → Redis Streams (partitioned by `source_id` for per-source
ordering) → M workers in a consumer group → PostgreSQL. This was not just designed but **run** in
this build: `docker-compose.yml` brings up 2 worker replicas plus the API against one shared Redis
queue, verified healthy and correctly processing distributed ingest traffic
(`tests/e2e/test_deployed_stack.py:test_health_reports_redis_backed_queue`, and manual `docker
compose ps`/`curl` verification during this build showed `uli-worker-1`/`uli-worker-2` both up
against `uli-redis-1`). No multi-worker *throughput* benchmark (events/sec across N workers under
load) was run — only functional correctness under the distributed topology. A quantified
horizontal-scaling number (e.g. events/sec vs. worker count) is a natural next benchmark, not yet
done.

## 5. Docker image sizes (measured, this build)

| Image | Size | Build time |
|---|---|---|
| `uli-api` | 441 MB | ~112s |
| `uli-worker` | 422 MB | ~29s (cache-assisted) |
| `uli-ml` | 687 MB | ~146s |
| `uli-collector` (Go, distroless) | 17.9 MB | ~58s |

See `docs/ml-strategy.md` §3 for why the ML sidecar is a separate, optional image rather than baked
into the base API/worker images.

## 6. Honest summary

At the single-process floor, this system sustains **~177-216 events/sec** with sub-11ms p99
latency up to 100K events, is memory-stable, and is CPU-bound on SQL statement overhead rather than
parsing or disk I/O — meaning the parsing ladder itself is not the bottleneck, the storage access
pattern is. The documented, demonstrated scaling answer is horizontal (more workers behind a shared
queue), not a faster single process; the single-process number here should be read as a
per-worker baseline for capacity planning, not as the system's ceiling.
