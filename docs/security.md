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

## 5. Bundle signing — implemented (Ed25519)

`uli/security/signing.py` implements Ed25519 sign/verify over `cryptography`'s
`Ed25519PrivateKey`/`Ed25519PublicKey` (the dependency was declared but unused before this; now it
is). Detached, base64-encoded signatures, never embedded/executed — a pack still cannot run code
even with a valid signature (§3's sandboxing is unrelated and still the primary mitigation for
malicious *content*; signing is about *origin*, not content safety).

- **CLI**: `python scripts/sign_bundle.py {keygen,sign,verify}` — generates an Ed25519 keypair,
  signs a file, or verifies a file+signature+pubkey. Same tool for parser packs and air-gap
  tarballs (`docs/air-gapped-deployment.md` §3).
- **API**: `POST /v1/parsers/bundles` accepts an optional `signature` field (base64, over the
  `yaml` field's UTF-8 bytes); `uli/api/app.py:load_bundle` passes it to `engine.register_pack`.
- **File-drop**: `ParserEngine.load_parsers_dir` looks for a sibling `<pack>.yaml.sig` next to each
  pack and verifies it the same way.
- **Verification**: `ParserEngine.register_pack` computes a real `bundle_sha256` (previously always
  `None`) and an honest `signature_ok` (previously **hardcoded `True` regardless of whether
  anything was checked** — that was the actual gap, not just "no code exists"). `signature_ok` is
  `True` only if `ULI_BUNDLE_PUBKEY_PATH` is configured, a signature was supplied, and it
  cryptographically verifies against that key; otherwise `False`.
- **Enforcement**: `ULI_BUNDLE_REQUIRE_SIGNATURE=true` makes `register_pack` raise (HTTP 422 via
  the API, a load error logged and skipped for file-drop) on a missing or invalid signature,
  instead of silently accepting an unverified pack.
- **Tests**: `tests/unit/test_signing.py` (roundtrip, tamper detection, wrong key, garbage input —
  all fail closed, never raise) and `tests/integration/test_bundle_signing.py` (the actual
  `register_pack`/`load_parsers_dir` enforcement paths).

Private keys are never committed (`deployment/keys/`, `*.pem` gitignored); operators generate their
own via `scripts/sign_bundle.py keygen` and distribute only the public key to verifying nodes.
Authentication (§2) and pack sandboxing (§3) remain the mitigations for a caller with no key at
all; signing adds non-repudiation and origin verification on top, for deployments that turn it on.

## 6. What this is / is not

No claim of novelty here — tenant isolation by column, static API-key auth, `safe_load`-only
config parsing, and regex timeouts are all standard, well-understood mitigations
(`research.md` covers no security-specific prior art comparison since none of this is being
presented as a research contribution). The scope of this document is accuracy about what runs
today, for a system whose primary contribution is elsewhere (parsing-ladder resilience and
unknown-vendor handling).

## 7. Known limitations (consolidated)

- Signing is opt-in, not the default (§5) — an operator who never sets `ULI_BUNDLE_PUBKEY_PATH` /
  `ULI_BUNDLE_REQUIRE_SIGNATURE` still gets the old accept-any-authenticated-YAML behavior.
- No TLS termination is configured in `docker-compose.yml`/`deployment/kubernetes/` — assumed to
  sit behind a reverse proxy or service mesh in any real deployment.
- No rate limiting on `/v1/ingest`.
- `auth=none` is the default; operators must explicitly opt into `static` mode and set
  `ULI_API_KEYS` for anything beyond local/demo use.
