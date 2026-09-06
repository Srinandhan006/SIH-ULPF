# Parser Design

## 1. Interface (every parser, every tier)

```python
class Parser(Protocol):
    id: str                      # "structural.json", "vendor.pfsense.filterlog"
    version: str                 # semver
    kind: ParserKind             # STRUCTURAL | DECLARATIVE | PYTHON | SUGGESTED
    tier: int                    # 1 or 2 (ladder tiers 3–7 are engine stages, not parsers)

    def can_parse(self, ctx: ParseContext) -> float: ...   # 0..1, cheap, never raises
    def parse(self, ctx: ParseContext) -> IR: ...           # never raises; sets ir.errors
    def metadata(self) -> ParserMetadata: ...              # vendor/product/signatures/compat
```

`ParseContext` carries: `raw: bytes`, `text: str` (decoded, replacement chars on bad UTF-8), `fingerprint`, `hints` (syslog PRI, transport), `source`. Parsers are pure with respect to the context; engine stages hold state (Drain3, caches).

`can_parse` is required to be **O(length)** and regex-free or anchored-prefix only (it runs for many parsers per unrouted event). `parse` may use regex through the guarded `regex` module with `timeout=ULI_REGEX_TIMEOUT_MS` (default 50 ms); a timeout yields `errors += ["regex_timeout"]` and falls through.

## 2. The ladder

```
                    bytes
                      │
             ┌────────▼────────┐
             │ decode + cap    │  UTF-8 w/ replacement; parse-view capped at 64 KiB (raw kept whole)
             └────────┬────────┘
             ┌────────▼────────┐
             │ Fingerprint     │  tokens → classes → shape, shape_hash, family_hash
             └────────┬────────┘
             ┌────────▼────────┐  hit → run routed parser directly (H3 routing cache)
             │ Routing cache   │
             └────────┬────────┘ miss
  T1 ┌────────────────▼────────────────┐
     │ Structural: JSON · CEF · LEEF · │ each returns can_parse ∈ {0, 0.5..0.9} from prefix/shape
     │ syslog5424 · syslog3164 · logfmt│ (e.g. "{"…"}" balanced → json 0.9; "CEF:0|" → 0.95)
     │ · CLF · OTLP-JSON · CSV-guess   │
     └────────────────┬────────────────┘
  T2 ┌────────────────▼────────────────┐
     │ Vendor packs (declarative YAML) │ signatures: literal prefixes, key sets, program names,
     │ + Python parsers                │ shape families → can_parse; extraction + OCSF mapping
     └────────────────┬────────────────┘
                      │ best = argmax(can_parse); execute; conf = f(match, fill, ts, schema)
  T3 ┌────────────────▼────────────────┐
     │ Field/type inference (always)   │ typed tokens (IP4/6, PORT, MAC, TS×14 grammars, URL,
     │                                 │ EMAIL, UUID, HEX, HASH, PATH, SEVERITY-WORD, KV pairs)
     │                                 │ → observables, timestamp, severity, extra fields
     └────────────────┬────────────────┘
                      │ conf < τ_known (0.6)?  no → normalize
  T4 ┌────────────────▼────────────────┐
     │ Drain3 template mining          │ per-source miner; masking from typed tokens;
     │                                 │ template_id, params; persisted state (file/Redis)
     └────────────────┬────────────────┘
  T5 ┌────────────────▼────────────────┐
     │ Similarity                      │ Jaccard on shape 3-grams vs known parser fingerprints;
     │                                 │ MinHash index; candidate parser if sim ≥ 0.8 → execute
     │                                 │ with conf *= sim
     └────────────────┬────────────────┘
  T6 ┌────────────────▼────────────────┐
     │ ML service (optional, HTTP)     │ vendor classifier P(vendor | shape, tokens);
     │  timeout 20 ms, circuit breaker │ anomaly scorer on template/param features
     └────────────────┬────────────────┘
  T7 ┌────────────────▼────────────────┐
     │ Quarantine                      │ UnknownRegistry.record(cluster by family_hash);
     │                                 │ event still normalized as OCSF Base Event (class 0)
     └────────────────┬────────────────┘
                      ▼
                 Normalizer → NormalizedEvent (always)
```

The order guarantees P1: the only way out of the ladder is a `NormalizedEvent`.

## 3. Fingerprinting

Tokenizer: split on whitespace and on structural delimiters (`=`, `:`, `,`, `|`, `[`, `]`, `(`, `)`, `"`), keeping delimiters as tokens. Classify each token by an ordered list of cheap checks (no regex for the hot path where possible; anchored regex otherwise):

`IP4 IP6 MAC PORT? TS NUM HEX UUID URL EMAIL PATH WORD PUNCT QUOTE` (+ `KV` when `WORD=…`).

- `shape` = class sequence with literal punctuation (e.g. `TS HOST WORD [ NUM ] : WORD = IP`).
- `shape_hash = sha1(shape)`.
- `family_hash = sha1(collapse_runs(shape without NUM/WORD distinctions))` — coarse cluster key.

Used by: routing cache (T0), drift histograms, unknown clustering, similarity (shape 3-gram sets).

## 4. Declarative vendor pack (YAML) — what an administrator writes or the system suggests

```yaml
parser_id: vendor.pfsense.filterlog
version: 1.2.0
vendor: Netgate
product: pfSense
compat: { envelope: uli.v1, ocsf: ">=1.6 <2" }
signatures:
  - kind: syslog_app        # matched against syslog appname/program
    equals: filterlog
  - kind: prefix
    startswith: "filterlog"
  - kind: shape_family
    equals: 5b1e…            # optional: fingerprint family learned at onboarding
extract:
  - kind: csv                # after syslog envelope: CSV body
    field: message
    columns: [rule, sub_rule, anchor, tracker, interface, reason, action, direction, ip_version, ...]
  - kind: regex              # guarded, timeout-bounded
    field: message
    pattern: '^(?P<rule>\d+),(?P<sub>[^,]*),(?P<anchor>[^,]*),(?P<tracker>\d+),(?P<iface>[^,]+),(?P<reason>[^,]+),(?P<action>pass|block),(?P<dir>in|out),(?P<ipver>4|6),'
map:
  class_uid: 4001
  fields:
    src_endpoint.ip:   { from: src_ip }
    src_endpoint.port: { from: src_port, type: int }
    dst_endpoint.ip:   { from: dst_ip }
    dst_endpoint.port: { from: dst_port, type: int }
    connection_info.protocol_name: { from: proto, lower: true }
    action_id: { from: action, enum: { pass: 1, block: 2 } }
    device.name: { from: syslog.hostname }
  unmapped: keep            # everything else → ocsf.unmapped
confidence:
  base: 0.9
  require: [src_ip, dst_ip]   # missing → confidence *= 0.5
```

The engine has exactly these `extract.kind`s: `csv`, `kv`, `regex`, `json_path`, `split`, `grok_lite` (a small curated pattern set). No code. `yaml.safe_load` only.

## 5. Confidence model

```
conf = base
     × match_quality      (signature strength: exact appname 1.0, prefix 0.9, shape family 0.8)
     × fill_ratio^0.5     (fraction of mapped fields present)
     × (0.5 if any `require` field missing)
     × ts_ok ? 1 : 0.85
     × schema_ok ? 1 : 0.9
```

Tier 4 events start at 0.5 and gain up to +0.1 from inference richness (≥3 typed fields). Tier 7 ≤ 0.3. Every factor is recorded in `provenance.confidence_factors` for explainability.

## 6. Drift handling inside the engine

- `DriftMonitor.observe()` receives `(source_id, parser_id, shape_hash, confidence, fill_vector)` per event; windows are aggregated in memory and flushed to storage.
- When a source is `degraded`, the engine lowers that parser's `can_parse` by ×0.7 so a structurally better tier (e.g. a *suggested* v2 parser in `candidate` status running in **shadow mode**) can win on confidence. Shadow candidates are executed but their output is stored under `provenance.shadow_of` for comparison, not as the primary event.
- Promotion flips the candidate to `active` and captures a new reference histogram.

## 7. Parser evolution algorithm (H1, detailed)

Input: `P_v1` (declarative pack, with its extraction producing named fields at known token positions on its reference template `t1`), drifted templates `T'` from Drain3.

For each `t'`:
1. `a = typed(t1)`, `b = typed(t')` — sequences of `(token_class, literal_if_punct)`.
2. Needleman–Wunsch: match +2 (same class & literal), compatible +1 (NUM↔PORT, WORD↔HOST), mismatch −2, gap −1.
3. Walk the alignment: for each labeled position in `a` (label = field name from `P_v1.extract`), if aligned to a non-gap in `b` with compatible class → carry the label to `b`'s position; else mark `lost`.
4. New positions in `b` with no label → `unmapped_N` with inferred type; if the token is a KV pair, the key becomes the name.
5. Emit `P_v2` YAML = `P_v1` with regenerated `extract` (positional `split`/`regex` synthesized from `b`) and unchanged `map` for carried labels; `score = carried / labeled_in_v1`.
6. `score ≥ 0.8` → `ParserSuggestion(origin="drift")` and shadow execution; `< 0.8` → suggestion only.

Validation plan: `tests/unit/test_evolution.py` on synthetic v1→v2→v3 mutations; metric = carried-label accuracy vs ground truth.

## 8. Hot-loading a new vendor

```
bundle.tar.gz
├── manifest.json     { parser_id, version, compat, sha256 of each file, signature (ed25519) }
├── parser.yaml
├── samples/*.log     (≥ 20 lines; used for validation + reference histogram)
└── README.md
```

`POST /v1/parsers/bundles` (or drop into `parsers/vendors/_inbox/`): verify signature → verify hashes → `yaml.safe_load` → schema-validate the pack → compat check → **dry-run on samples** (must reach conf ≥ 0.7 on ≥ 90 % of samples) → register as `active` → routing cache invalidated for its signatures → `Audit(bundle.load)`. No process restart. Failure at any step → bundle rejected with a structured reason; the running registry is untouched.
