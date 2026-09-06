# Kubernetes manifests (reference, not production-hardened)

Minimal manifests to run the same images as `docker-compose.yml` on a cluster. Apply in order:

```
kubectl apply -f namespace.yaml
kubectl apply -f configmap.yaml
kubectl apply -f redis.yaml
kubectl apply -f api.yaml
kubectl apply -f worker.yaml
```

Images (`uli-api:latest`, `uli-worker:latest`) must already be pushed to a registry the cluster can
pull from, or pre-loaded onto every node (air-gapped clusters: see `docs/air-gapped-deployment.md`
and `deployment/airgap/build_bundle.sh`, then `ctr images import` / `crictl` per your CRI instead of
`docker load`).

## Important caveat: default storage backend is SQLite

`uli-data` is a single `ReadWriteOnce` PVC shared by the `api` deployment and both `worker`
replicas. This is fine for a **single-node** cluster (kind/minikube/k3s) with the default SQLite
backend, matching the demo/hackathon scope this repo targets — it is **not** a safe multi-node
configuration: SQLite does not tolerate concurrent writers from different nodes, and most
`ReadWriteOnce` storage classes cannot even schedule multiple pods across nodes onto the same
volume.

For a real multi-node deployment, per `architecture.md` §6/§7:
- Set `ULI_DB_URL` (in `configmap.yaml` or a Secret) to a PostgreSQL connection string — the
  storage layer is the same SQLAlchemy code, dialect switch only (`pip install ".[postgres]"`,
  already declared as an optional dependency in `pyproject.toml`).
- Point the raw store at object storage once that adapter exists (`architecture.md` §6 lists it as
  roadmap, interface-only today) instead of the local-PVC segment files.

Not included here (out of scope for this build): Ingress/TLS termination, HorizontalPodAutoscaler,
NetworkPolicy, and the `ml`/`collector` deployments (add them following the same pattern as
`api.yaml`/`worker.yaml`, pointing at `Dockerfile.ml`/`Dockerfile.collector`).
