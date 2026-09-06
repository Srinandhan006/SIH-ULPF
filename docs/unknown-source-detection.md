# Unknown-source detection

Companion to `architecture.md` §4/§9 and `research.md` §4. Covers what happens when a log line
fails tiers 1-3 (structural, declarative vendor pack, field inference) and lands in tiers 4-7.

## 1. Pipeline gate

`uli/pipeline.py` records a line into the unknown registry when:

```python
if ir.tier >= 4 and ir.confidence < settings.tau_known:  # tau_known default 0.6
    record_unknown(storage, tenant_id, source_id, ctx, ir, raw_event_id, settings.unknown_suggest_min_events)
```

This threshold is deliberate, not incidental: a line that a declarative or KV parser matches with
`confidence >= tau_known` is treated as "known enough" even if no vendor pack names it explicitly —
tiers 4-7 (Drain3 template mining, shape similarity, optional ML sidecar, quarantine) are exactly
the tiers whose output is *never* trusted as "identified", so every event they produce is eligible
for clustering. This was fixed during hardening: the gate originally only fired for tiers 6-7,
which contradicted the tiers-4-7 requirement in `architecture.md` §9 and silently dropped
Drain3-tier (tier 4) unknowns from the registry. Regression coverage:
`tests/integration/test_unknown_and_evolution.py`.

## 2. Clustering key: `family_hash`

`uli/detection/unknown.py:record_unknown` groups events by `ctx.fingerprint.family_hash` — a
coarser hash than `shape_hash` (built from `uli/fingerprint.py`), deliberately tolerant of small
per-line variation (a counter, an IP, a timestamp) so that 1,000 lines from one never-seen vendor
collapse into one cluster instead of 1,000 singleton clusters. Each cluster accumulates:

| Field | Purpose |
|---|---|
| `shape_hashes` (capped 100) | distinct structural shapes seen, for drift-within-unknown visibility |
| `template_ids` / `templates` | Drain3 template strings (`<NUM>`/`<TS>`-style wildcards) — the human-readable summary of "what this format looks like" |
| `inferred_fields` | per-field fill-rate + one example + inferred Python type, built incrementally by `_merge_inferred` |
| `event_count` | drives the suggestion trigger |
| `confidence` | last IR confidence seen (tier 4-7 confidence, always < `tau_known`) |
| `example_raw_event_id` | pointer into the raw store for a human to go read the actual bytes |

## 3. Suggestion trigger

When `event_count == settings.unknown_suggest_min_events` (default 20) and the cluster is still
`status="open"`, `record_unknown` returns the cluster id to the caller, which invokes suggestion
synthesis (`POST /v1/unknown/{cluster_id}/suggest`, or automatically in the pipeline — see
`uli/api/app.py:suggest`). This reuses the **same** template→typed-alignment→YAML machinery as
parser evolution (`uli/drift/evolution.py`) described in `architecture.md` §5.2 — for a fresh
unknown vendor there is no v1 pack to align against, so alignment is against nothing and the
synthesized pack is a first-pass declarative skeleton (field names taken from `inferred_fields`,
one signature clause built from `family_hash`) rather than a diff-and-patch. This is a deliberate
reuse decision: one alignment/synthesis engine, two call sites (drift-driven and unknown-driven),
not two parallel implementations.

## 4. Human-in-the-loop promotion

The synthesized pack never becomes `active` on its own. It is written as a `ParserSuggestion` row
(`origin="unknown"`), visible at `GET /v1/parsers/suggestions`, and only becomes a loaded,
routable parser after `POST /v1/parsers/suggestions/{id}/promote {"approved_by": "..."}`. This is
intentional: an auto-promoted parser trained on 20 lines from one cluster could silently start
misrouting adjacent, superficially similar traffic. Promotion records `approved_by` for audit.
Verified end-to-end (ingest → cluster → suggest → promote → parser now routes new lines) in
`tests/e2e/test_deployed_stack.py:test_unknown_cluster_can_be_promoted_to_active_parser`.

## 5. What this is / is not

Per `research.md` §9 (Exact Research Gap): per-line unknown-format handling via a parsing ladder,
Drain3-based template mining, and human-approved suggestion generation are all prior art we
**reuse**, not invent. The **combination** — that failing all deterministic tiers still produces a
usable OCSF event *and* a growing, auditable cluster with a promotable draft parser, in one
pipeline pass, with no distinct "unknown log batch job" — is the part we have not found already
assembled as one open-source system. We do not claim the underlying clustering or template-mining
algorithms are novel.

## 6. Known limitations

- `family_hash` collisions across genuinely different vendors that happen to share coarse shape
  (e.g., two space-delimited five-token formats) would merge into one cluster; the operator sees
  this as a bad synthesized pack draft (low `score`, weird field names) before promoting.
- No automatic cluster-splitting once merged; only manual promotion or (roadmap) an admin
  "split cluster" action.
- Suggestion quality is bounded by 20 sample lines by default; `unknown_suggest_min_events` is
  operator-tunable per deployment.
