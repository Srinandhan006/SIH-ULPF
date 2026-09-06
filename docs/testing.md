# Testing

Five layers, each with a distinct purpose — the point of keeping them separate is that they catch
different classes of bug (see §5 for a real example of a bug only the HTTP-layer tests caught).

| Layer | Command | What it exercises | Count (this build) |
|---|---|---|---|
| Unit | `make test-unit` (`pytest tests/unit -v`) | Individual functions/classes in isolation: fingerprinting, timestamp parsing, structural/declarative parsers, the ladder engine, drift detection math, parser evolution alignment. | 43 tests, 7 files |
| Integration | `make test-integration` | Full in-process pipeline (`uli.bootstrap.build_stack`) — ingest → ladder → normalize → storage → provenance, unknown-vendor clustering end to end, resource-drift diffing, and the HTTP layer via FastAPI's `TestClient` (no real network). | 20 tests (parametrized), 4 files |
| Adversarial | `make test-adversarial` (`pytest tests/adversarial`) | Hostile/malformed input: truncated lines, binary garbage, oversize lines, malformed vendor packs loaded mid-flight, pathological regex input — every case asserts the pipeline still returns a usable event or a quarantine event, never an exception. | 21 tests (parametrized), 1 file |
| E2E | `make test-e2e` (`pytest tests/e2e -m e2e`) | Real `docker compose up` deployment over actual HTTP (`httpx.Client`, not `TestClient`) — known vendor, format drift, unknown-vendor cluster→suggest→promote, raw provenance round-trip, stats/metrics. Skips cleanly (not fails) if `localhost:8080` isn't reachable, so it never breaks a plain `pytest` run on a machine without the stack up. | 8 tests, 1 file |
| Performance | `make test-performance` | Real, measured throughput/latency/CPU/memory at 1K/10K/100K events — see `docs/scalability.md`. Never asserts a specific throughput number (only that it's positive and p99 latency stays under a generous 5s ceiling); the numbers are read and copied into docs by hand, never used to gate CI. | 1 parametrized test × 3 scales |

Run everything except the slow performance suite: `make test` (`pytest -m "not performance"`) — 84
tests, 0 failures, in this build.

## 1. Why five layers instead of one big suite

Unit tests are fast and pinpoint exactly which function broke, but they cannot catch a bug in how
components are *wired together* (serialization at a boundary, a dependency not installed in the
image, a config default that only matters when two services actually talk to each other).
Integration tests catch wiring bugs but run in-process, so they cannot catch a bug that only exists
in the real container image or the real network path. E2E tests catch exactly that, but are slow
and require a running stack, so they're not the first thing a developer runs on every save.
Adversarial tests are unit/integration-style but organized around a different question ("can this
input break the system") rather than "does this feature work" — worth keeping as its own layer
because normal feature tests naturally drift toward happy-path inputs.

## 2. A concrete example: the bug only the HTTP-layer test caught

`GET /v1/raw/{raw_event_id}` returned HTTP 500 (`TypeError: Object of type datetime is not JSON
serializable`) despite 43 unit tests and the pre-existing integration suite all passing. The
existing provenance round-trip test (`tests/integration/test_pipeline_end_to_end.py`) called
`stack.storage.get_raw_location()` **directly** — it never went through the actual FastAPI response
serialization path, because `uli/api/app.py:get_raw` manually built a `JSONResponse(dict)`
containing raw `datetime` objects, bypassing FastAPI's automatic `jsonable_encoder`. This was only
discovered by live-testing the deployed Docker stack with `curl` and reading
`docker compose logs api`. Fixed in `uli/api/app.py` (wrap the body in `jsonable_encoder(...)`
before constructing `JSONResponse`), and closed permanently with a new regression test file,
`tests/integration/test_api_routes.py`, which exercises `/v1/ingest`, `/v1/events`, `/v1/raw`,
`/v1/sources`, `/v1/stats`, `/health`, and `/metrics` through the actual HTTP/JSON-serialization
path (`api_client` fixture) rather than direct method calls. This is the concrete justification for
keeping E2E as a real, separate layer rather than assuming integration tests are sufficient.

## 3. Non-fabrication policy for performance numbers

`tests/performance/test_throughput.py`'s own docstring states the rule directly: results are
written to `tests/performance/last_run_results.json`, read by a human, and copied verbatim into
`docs/scalability.md` — that file "must never be edited to make a number look better; the fix for
a bad number is a real code change, re-run, and a doc update citing the new run." This is enforced
by convention (there is no separate mechanism preventing hand-editing), documented here so it is
explicit rather than assumed.

## 4. Fixtures

`tests/conftest.py` provides `stack` (an in-process `uli.bootstrap.build_stack()` against a
temp-dir SQLite DB, torn down per test) and `api_client` (a FastAPI `TestClient` wrapping the same
stack) — the latter was defined early but unused until `test_api_routes.py` was added (see §2).
`tests/e2e/conftest.py` provides `client`, an `httpx.Client` against a real running stack, and a
session-scoped `base_url` fixture that calls `pytest.skip()` (not a failure) if nothing answers on
`localhost:8080`.

## 5. What is not covered yet

- No mutation testing or coverage-percentage gate is configured — the suite is designed around
  "does every documented requirement and every discovered bug have a named test", not a coverage
  threshold.
- No load/soak test beyond the single-process throughput measurements in `tests/performance/`
  (no multi-hour or multi-process sustained-load run).
- No fuzzing harness (e.g. `hypothesis`) — adversarial inputs are hand-curated, not generated.
