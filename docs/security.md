# Security

Summary referenced by `architecture.md` §8. States what is implemented and verified vs. declared
in config but not yet enforced — per the project's honesty principle, the latter is not glossed
over.

## 1. Multi-tenancy

`tenant_id` is a column on every table (`uli/storage/sql.py`) and a path prefix on every raw
segment. `uli/api/auth.py:tenant_id` is the single source of truth for tenant identity — it is
**never** read from the request body, only from the authenticated identity (API key → tenant map,
or the fixed default tenant in `auth=none` mode). Every storage/query call in `uli/api/app.py`
takes `tid` from this dependency, so cross-tenant reads are prevented by construction, not by a
per-query filter an author could forget to add — confirmed by inspection of every route in
`uli/api/app.py` (`ingest`, `events`, `get_event`, `get_raw`, `sources`, `unknown`, `drift`, `stats`
all thread `tid` through).

## 2. Authentication

Two modes (`Settings.auth`): `none` (default — all requests map to `default_tenant`; intended for
local/demo, not multi-tenant production) and `static` (`X-API-Key` header checked against
`ULI_API_KEYS`, a `key:tenant` map). This is **static-key auth, not OAuth/mTLS/SSO** — adequate for
a hackathon/demo deployment and for service-to-service calls inside a trusted network, not for a
public-facing production API. Tracked as a roadmap item, not claimed otherwise.

## 3. Parser-pack safety (the actual attack surface for "untrusted config")

Vendor packs are declarative YAML, loaded with `yaml.safe_load` (never `yaml.load`/`eval`/`exec`)
— a malicious or malformed pack cannot execute code, only describe field extraction. Two specific
protections, both verified by the adversarial test suite (`tests/adversarial/`):

- **ReDoS**: every pack regex runs through the `regex` module (not stdlib `re`), which supports a
  **wall-clock timeout** — `uli/parsers/base.py:guarded_search` calls `pat.search(text,
  timeout=timeout_ms/1000.0)` (default `regex_timeout_ms=50`) and treats a timeout as "this parser
  doesn't match", never as a crash or hang. This is real backpressure against catastrophic
  backtracking, not just a code comment.
- **Malformed packs never crash the engine**: `uli/api/app.py:load_bundle` wraps
  `engine.register_pack` in a broad `except Exception` and returns HTTP 422, and
  `tests/integration/test_unknown_and_evolution.py:test_parser_never_raises_even_when_pack_is_malformed_after_load`
  exercises a pack that becomes malformed mid-flight.

## 4. Input bounds

`max_line_bytes` (64 KiB) caps what any parser tier sees per line — oversize input is truncated
*for parsing only*; the untruncated raw bytes are still stored whole in the raw store (provenance
is never lossy, only the parse attempt is bounded). `max_ingest_body_bytes` (8 MiB) caps request
body size at the API layer.

## 5. Bundle signing — declared, not implemented (be precise about this)

`Settings.bundle_pubkey_path` / `bundle_require_signature` exist as config fields, and
`architecture.md` §8 states the target design ("Bundles are Ed25519-signed"). **No signature
generation or verification code exists in this build** — `cryptography>=42` is declared in
`pyproject.toml`'s main dependencies (evidence the intent was there) but is never imported anywhere
under `uli/` (confirmed by grep across the whole package); there is no Ed25519/`nacl` usage either. `POST /v1/parsers/bundles` accepts any syntactically valid YAML pack from
an authenticated tenant with no signature check. This is the single biggest gap between documented
intent and implemented behavior in the system, and it is recorded here and in
`docs/roadmap.md`/`docs/air-gapped-deployment.md` rather than silently left for someone to
discover. Mitigation today is authentication (§2) plus the sandboxing in §3 (a pack cannot execute
code even if it comes from a compromised or malicious source) — signing would add non-repudiation
and origin verification on top of that, not replace it.

## 6. What this is / is not

No claim of novelty here — tenant isolation by column, static API-key auth, `safe_load`-only
config parsing, and regex timeouts are all standard, well-understood mitigations
(`research.md` covers no security-specific prior art comparison since none of this is being
presented as a research contribution). The scope of this document is accuracy about what runs
today, for a system whose primary contribution is elsewhere (parsing-ladder resilience and
unknown-vendor handling).

## 7. Known limitations (consolidated)

- No bundle signing (§5).
- No TLS termination is configured in `docker-compose.yml`/`deployment/kubernetes/` — assumed to
  sit behind a reverse proxy or service mesh in any real deployment.
- No rate limiting on `/v1/ingest`.
- `auth=none` is the default; operators must explicitly opt into `static` mode and set
  `ULI_API_KEYS` for anything beyond local/demo use.
