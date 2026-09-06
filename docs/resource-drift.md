# Resource / configuration drift

Companion to `architecture.md` §5.3. This is distinct from **parser drift** (`architecture.md`
§5.1 — is the *log format* changing) — resource drift asks whether the *deployment* (host
capacity, running containers, config) has changed from what was declared as expected.

## 1. Desired vs. observed

`uli/drift/resource.py:ResourceDriftAgent.tick()` runs on a timer (`resource_drift_interval_s`,
default 30s):

1. `load_desired_state(settings.desired_state_path)` reads `deployment/desired-state.yaml`. If the
   file is absent, or a key is simply not present, that key is **never compared** — this is a
   deliberate zero-false-positive default. The shipped template
   (`deployment/desired-state.yaml`) has every key commented out for exactly this reason: a fresh
   deployment produces zero drift events until an operator opts in by uncommenting values that
   describe *their* environment's capacity plan.
2. `observe_system()` collects: CPU count and total memory (`psutil`), disk usage, `ULI_*`
   environment variables, and container state from the Docker Engine API (see §2).
3. `diff(desired, observed)` produces typed `DriftEvent`s (kind: `cpu`, `memory`, `config`,
   `image`, `env`; severity: `low`/`medium`/`high`).
4. The observed snapshot itself is content-hashed and written through the **same raw store and
   provenance envelope** as ingested logs (`raw_event_id` = sha256 of the snapshot JSON) — so a
   resource-drift event is forensically traceable back to the exact system snapshot that produced
   it, not just a summary string. This reuses `RawRecord`/`raw_store.append` rather than a parallel
   storage path.

## 2. Docker Engine API — deliberately not mounted in this deployment

`_docker_state()` in `uli/drift/resource.py` probes `/var/run/docker.sock` via the Docker Engine
HTTP API (`GET /containers/json`) to compare running container image digests against
`desired-state.yaml`'s `containers:` section. **This socket is deliberately not mounted into the
`api`/`worker` containers in `docker-compose.yml`** in this build: giving a container the host
Docker socket is equivalent to giving it root on the host (it can start/stop/inspect *any*
container), and that tradeoff was judged not worth it for a demo/hackathon deployment.

Consequence: `_docker_state()` returns `{}` (no crash, no drift events for containers) whenever the
socket is absent — confirmed by the `if not sock.exists(): return {}` guard. This is intentional
graceful degradation, not a bug: resource drift for CPU/memory/env still works fully without the
socket; only container-image-digest drift is inactive. An operator who wants that signal can mount
the socket explicitly (`- /var/run/docker.sock:/var/run/docker.sock:ro`) and accept the tradeoff,
or run the agent on the Docker host directly outside a container.

## 3. Severity and storage

Every `DriftEvent` carries `desired`, `observed`, `severity`, and `snapshot_raw_event_id`, and is
written via `storage.write_drift` into the same tenant-scoped event store as normalized logs —
queryable at `GET /v1/drift?kind=...`. `DRIFT_EVENTS` Prometheus counter is labeled by
`kind`/`severity` for alerting.

## 4. What this is / is not

Per `research.md` §7: desired-state-vs-observed drift detection for infrastructure is a mature,
well-understood pattern (Chef InSpec, AWS Config, Kubernetes admission controllers). We do not
claim novelty in the *comparison* — the contribution here (see `research.md` §9.2) is that
resource-drift events live in the **same** event store, schema, and provenance envelope as
parsed-log events and parser-drift events, so an operator investigating an incident sees all three
drift types correlated on one timeline (`architecture.md` §5.4, Correlator) instead of stitched
together from three separate tools.

## 5. Known limitations

- Kubernetes API probing is not implemented in this build (`architecture.md` mentions it as a
  future adapter alongside the Docker Engine API); only Docker and bare `psutil` signals are live.
- No automatic remediation — by design (`architecture.md` §5.4: "Never auto-remediates; produces
  evidence").
- `env` drift only compares variables prefixed `ULI_`; arbitrary environment drift outside that
  prefix is out of scope to avoid leaking unrelated secrets into drift event bodies.
