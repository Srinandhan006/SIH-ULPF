# Air-gapped deployment

Every piece of the system is designed to run with zero outbound network access at runtime. This
doc covers what "air-gapped" means concretely here, how the offline bundle is built, and what is
verified vs. what is a documented limitation.

## 1. What makes this air-gap-safe by construction

- **No model downloads.** The optional ML sidecar (`docs/ml-strategy.md`) trains online from local
  traffic only — `IsolationForest` and nearest-centroid, both fit in-process, no pretrained weights
  fetched from anywhere. `ULI_ML_ENABLED=false` by default.
- **No package installs at container start.** All three Python images (`Dockerfile.api/.worker/.ml`)
  are multi-stage builds — dependencies are `pip install`ed at *build* time into the image; nothing
  is fetched at container run time.
- **No external API calls in the parsing ladder.** Every tier (structural, declarative, Drain3,
  shape-similarity, ML sidecar, quarantine) runs against local code and local data.
- **Vendor packs are local YAML files**, hot-loaded from `parsers/vendors/` — adding a vendor is a
  file drop, not a registry pull.
- **The Go collector is a static, `CGO_ENABLED=0` binary** on a `distroless/static` base
  (17.9 MB image) — no shared libraries, no package manager, minimal attack surface for an
  edge-deployed component.

## 2. Building the offline bundle

`deployment/airgap/build_bundle.sh`:

1. `docker compose build` the four images (api, worker, ml, collector).
2. `docker save` each into a tarball (no registry needed at the destination).
3. Copies `parsers/vendors/`, `schemas/`, `logs/samples/` (with each dataset's `LICENSE-NOTE.txt`
   — see `logs/samples/README.md`), `docs/`, `docker-compose.yml`, and `deployment/desired-state.yaml`
   into a staging directory, plus a generated `LOAD.sh` and `MANIFEST.txt`.
4. Archives to `dist/uli-airgap-<UTC timestamp>.tar.gz` and appends its sha256 to
   `dist/SHA256SUMS.txt`.

Run via `make airgap-bundle`. On the air-gapped target: verify the checksum, extract, then run the
generated `LOAD.sh`, which `docker load`s every image tarball and runs
`docker compose -f deployment/docker-compose.yml up -d` — no `docker compose build` step is needed
or possible offline, since building would require pulling `python:3.12-slim`/`golang:1.22-alpine`
base layers that are only baked in at bundle-build time on a networked machine.

## 3. Integrity and authenticity

The bundle is checksummed (SHA-256, `SHA256SUMS.txt`) so transfer corruption is caught, and it can
now also be **Ed25519-signed** (`uli/security/signing.py`, `scripts/sign_bundle.py`):

```
python3 scripts/sign_bundle.py keygen --out-dir deployment/keys
ULI_AIRGAP_SIGNING_KEY=deployment/keys/bundle_private.pem bash deployment/airgap/build_bundle.sh
bash deployment/airgap/verify_bundle.sh dist/uli-airgap-<TAG>.tar.gz deployment/keys/bundle_public.pem
```

`build_bundle.sh` signs the final tarball when `ULI_AIRGAP_SIGNING_KEY` is set (and prints an
explicit warning, not a silent pass, when it is not); `verify_bundle.sh` is the target-side check —
checksum first, then signature if a `.sig` and public key are present. The same mechanism covers
individual vendor parser packs: `POST /v1/parsers/bundles` and `ParserEngine.load_parsers_dir`
(sibling `<pack>.yaml.sig` files) both verify against `ULI_BUNDLE_PUBKEY_PATH` and honestly record
`signature_ok` per parser (`uli/storage/sql.py:parsers.signature_ok`) — this used to be hardcoded
`True` regardless of whether anything was actually checked; see `docs/security.md` §5 for what
changed and `tests/unit/test_signing.py` / `tests/integration/test_bundle_signing.py` for the
verification. Setting `ULI_BUNDLE_REQUIRE_SIGNATURE=true` makes an invalid or missing signature a
hard rejection (`ParserEngine.register_pack` raises) rather than an accepted-but-unverified load.

The private key is never committed to the repo (`deployment/keys/`, `*.pem` are gitignored) —
operators generate and hold their own per `docs/security.md` §5.

## 4. What was verified in this build

- `docker compose build` for all four services succeeds from a clean checkout (measured sizes and
  build times in `docs/ml-strategy.md` §3).
- The full compose stack (`redis` + `api` + `worker`×2) starts, passes healthchecks, and correctly
  ingests/parses/serves events with **no internet access required after images are built** — the
  containers were run without any outbound calls in the ingest/parse/query path.
- `deployment/airgap/build_bundle.sh` was syntax-checked (`bash -n`); a full build+bundle+reload
  cycle on a second, genuinely offline host was not performed in this session due to time budget —
  documented here rather than claimed as verified.

## 5. Known limitations

- Signing is opt-in (`ULI_AIRGAP_SIGNING_KEY` / `ULI_BUNDLE_REQUIRE_SIGNATURE`), not the default —
  an operator who doesn't set these still gets a checksummed-only bundle, same as before. The
  mechanism now exists and is tested; enforcing it everywhere by default is a deployment-config
  choice, not a missing capability.
- Kubernetes air-gapped image loading (`ctr`/`crictl import`) is documented (`deployment/kubernetes/README.md`)
  but not scripted — only the Docker Compose `LOAD.sh` path is automated.
- `logs/samples/` datasets are for **development and demo only**; a production air-gapped
  deployment would not ship third-party research datasets in its runtime bundle. `build_bundle.sh`
  includes them today for demo-reproducibility; excluding them is a one-line change if a leaner
  production bundle is needed.
