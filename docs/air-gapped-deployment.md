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

## 3. Integrity, not authenticity — an honest gap

The bundle is checksummed (SHA-256) so transfer corruption is caught. It is **not currently
code-signed**. `uli/config.py` has `bundle_pubkey_path` / `bundle_require_signature` fields, and
`architecture.md` §8 describes "Bundles are Ed25519-signed" as the target design, but no signature
generation or verification code exists yet in this build — `POST /v1/parsers/bundles`
(`uli/api/app.py:load_bundle`) accepts a YAML pack and registers it with no signature check. This
is called out explicitly rather than left to be discovered: **do not present bundle signing as
implemented.** Tracked in `docs/roadmap.md` and `docs/security.md`.

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

- No bundle signing yet (§3).
- Kubernetes air-gapped image loading (`ctr`/`crictl import`) is documented (`deployment/kubernetes/README.md`)
  but not scripted — only the Docker Compose `LOAD.sh` path is automated.
- `logs/samples/` datasets are for **development and demo only**; a production air-gapped
  deployment would not ship third-party research datasets in its runtime bundle. `build_bundle.sh`
  includes them today for demo-reproducibility; excluding them is a one-line change if a leaner
  production bundle is needed.
