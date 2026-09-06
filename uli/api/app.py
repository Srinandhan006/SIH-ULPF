"""FastAPI application: POST /ingest, GET /events, /events/{id}, /raw/{id}, /sources, /parsers,
/parsers/suggestions, /parsers/{id}/promote, /unknown, /drift, /health, /metrics.

Ingest is synchronous-through-the-pipeline in `mode=local` (single container demo, no queue hop
needed for correctness) and queue-publishing in `mode=distributed`. Either way it never blocks on
downstream slowness beyond the queue publish itself, and never 5xxs on bad log content — P1 holds
at the API boundary too: worst case is a low-confidence quarantined event, not an HTTP error.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from uli.api.auth import tenant_id
from uli.api.schemas import BundleRequest, IngestRequest, IngestResponse, PromoteRequest
from uli.bootstrap import Stack, build_stack
from uli.drift.evolution import align, synthesize_pack_yaml, transfer_labels
from uli.fingerprint import tokenize
from uli.logging import get_logger
from uli.metrics import render as render_metrics
from uli.models import RawEnvelope

log = get_logger("api")


def create_app(stack: Stack | None = None) -> FastAPI:
    app = FastAPI(title="Universal Log Intelligence", version="0.1.0")
    app.state.stack = stack or build_stack()
    app.state.settings = app.state.stack.settings

    S = lambda: app.state.stack  # noqa: E731

    @app.get("/health")
    def health() -> dict:
        st = S()
        return {"ok": True, "db": st.storage.health(), "queue": st.queue.health(), "parsers": len(st.engine.declarative) + len(st.engine.structural), "mode": st.settings.mode}

    @app.get("/metrics")
    def metrics() -> Response:
        body, ctype = render_metrics()
        return Response(content=body, media_type=ctype)

    @app.post("/v1/ingest", response_model=IngestResponse)
    def ingest(req: IngestRequest, tid: str = Depends(tenant_id)) -> IngestResponse:
        st = S()
        lines = req.lines if req.lines is not None else ([req.text] if req.text is not None else [])
        if not lines:
            raise HTTPException(status_code=400, detail="provide 'lines' or 'text'")
        total_bytes = sum(len(l.encode("utf-8", "replace")) for l in lines)
        if total_bytes > st.settings.max_ingest_body_bytes:
            raise HTTPException(status_code=413, detail="payload too large")
        event_ids: list[str] = []
        for line in lines:
            env = RawEnvelope.from_bytes(line.encode("utf-8", "replace"), tenant_id=tid, source_id=req.source_id, transport=req.transport, hints=req.hints)
            if st.settings.mode == "distributed":
                st.queue.publish(env)
            else:
                ev = st.pipeline.process(env)
                event_ids.append(ev.event_id)
        return IngestResponse(accepted=len(lines), queued=(st.settings.mode == "distributed"), event_ids=event_ids)

    @app.get("/v1/events")
    def list_events(
        source_id: str | None = None, tier: int | None = None, class_uid: int | None = None, parser_id: str | None = None,
        min_confidence: float | None = None, max_confidence: float | None = None, q: str | None = None,
        limit: int = Query(100, le=1000), offset: int = 0, tid: str = Depends(tenant_id),
    ) -> list[dict]:
        return S().storage.query_events(tid, source_id=source_id, tier=tier, class_uid=class_uid, parser_id=parser_id, min_confidence=min_confidence, max_confidence=max_confidence, q=q, limit=limit, offset=offset)

    @app.get("/v1/events/{event_id}")
    def get_event(event_id: str, tid: str = Depends(tenant_id)) -> dict:
        ev = S().storage.get_event(tid, event_id)
        if ev is None:
            raise HTTPException(status_code=404, detail="event not found")
        return ev

    @app.get("/v1/raw/{raw_event_id}")
    def get_raw(raw_event_id: str, tid: str = Depends(tenant_id)) -> Response:
        st = S()
        found = st.storage.get_raw_location(tid, raw_event_id)
        if found is None:
            raise HTTPException(status_code=404, detail="raw event not found")
        loc, meta = found
        data = st.raw_store.read(loc)
        ok = st.raw_store.verify(loc, raw_event_id)
        body = {"raw_event_id": raw_event_id, "sha256_verified": ok, "length": len(data), "payload": data.decode("utf-8", "replace"), "meta": {k: v for k, v in meta.items() if k not in ("segment", "offset")}}
        return JSONResponse(jsonable_encoder(body))

    @app.get("/v1/sources")
    def sources(tid: str = Depends(tenant_id)) -> list[dict]:
        return S().storage.list_sources(tid)

    @app.get("/v1/parsers")
    def parsers(status: str | None = None) -> list[dict]:
        return S().storage.list_parsers(status)

    @app.post("/v1/parsers/bundles")
    def load_bundle(req: BundleRequest, tid: str = Depends(tenant_id)) -> dict:
        st = S()
        try:
            p = st.engine.register_pack(req.yaml, origin="api")
        except Exception as e:  # noqa: BLE001 — a bad pack must be rejected, never crash the process
            raise HTTPException(status_code=422, detail=f"pack rejected: {e}")
        return {"parser_id": p.id, "version": p.version, "status": "active"}

    @app.delete("/v1/parsers/{parser_id}")
    def retire_parser(parser_id: str, tid: str = Depends(tenant_id)) -> dict:
        ok = S().engine.retire_pack(parser_id)
        if not ok:
            raise HTTPException(status_code=404, detail="parser not loaded")
        return {"parser_id": parser_id, "status": "retired"}

    @app.get("/v1/unknown")
    def unknown(status: str | None = "open", tid: str = Depends(tenant_id)) -> list[dict]:
        return S().storage.list_unknown(tid, status)

    @app.get("/v1/unknown/{cluster_id}")
    def unknown_one(cluster_id: str) -> dict:
        c = S().storage.get_unknown(cluster_id)
        if c is None:
            raise HTTPException(status_code=404, detail="cluster not found")
        return c

    @app.post("/v1/unknown/{cluster_id}/suggest")
    def suggest(cluster_id: str, tid: str = Depends(tenant_id)) -> dict:
        """Synthesize a candidate parser from an unknown cluster's dominant Drain3 template using
        the same label-transfer machinery as drift evolution (H1), aligned against an empty base
        (i.e. pure field-position inference: every non-punct token becomes a named placeholder)."""
        st = S()
        cluster = st.storage.get_unknown(cluster_id)
        if cluster is None:
            raise HTTPException(status_code=404, detail="cluster not found")
        templates = cluster.get("templates") or {}
        if not templates:
            raise HTTPException(status_code=422, detail="no templates mined yet for this cluster")
        template = max(templates.values(), key=len)
        toks = tokenize(template)
        positions = {i: f"field_{i}" for i, t in enumerate(toks) if t not in ("<*>",) and t.strip(" :,|=[]()\"") != ""}
        base_spec = {"parser_id": f"suggested.{cluster['source_id']}.{cluster_id[:8]}".lower().replace(":", "_"), "version": "0.1.0",
                    "vendor": "unknown", "product": cluster["source_id"], "signatures": [{"kind": "shape_family", "equals": cluster["family_hash"], "required": True}],
                    "map": {"fields": {}}}
        yaml_text = synthesize_pack_yaml(base_spec, positions, toks, score=cluster.get("confidence") or 0.5)
        st.storage.add_suggestion(cluster_id=cluster_id, tenant_id=tid, source_id=cluster["source_id"], origin="unknown", base_parser_id=None, parser_id=base_spec["parser_id"], yaml=yaml_text, score=cluster.get("confidence") or 0.5, details={"template": template})
        st.storage.upsert_unknown_cluster(cluster_id=cluster_id, status="suggested")
        return {"cluster_id": cluster_id, "parser_id": base_spec["parser_id"], "yaml": yaml_text}

    @app.get("/v1/parsers/suggestions")
    def suggestions(status: str | None = None) -> list[dict]:
        return S().storage.list_suggestions(status)

    @app.get("/v1/parsers/suggestions/{suggestion_id}")
    def suggestion_one(suggestion_id: str) -> dict:
        s = S().storage.get_suggestion(suggestion_id)
        if s is None:
            raise HTTPException(status_code=404, detail="suggestion not found")
        return s

    @app.post("/v1/parsers/suggestions/{suggestion_id}/promote")
    def promote(suggestion_id: str, req: PromoteRequest, tid: str = Depends(tenant_id)) -> dict:
        st = S()
        sug = st.storage.get_suggestion(suggestion_id)
        if sug is None:
            raise HTTPException(status_code=404, detail="suggestion not found")
        try:
            p = st.engine.register_pack(sug["yaml"], origin=f"suggestion:{suggestion_id}", status="active")
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=422, detail=f"candidate pack invalid: {e}")
        st.storage.update_suggestion(suggestion_id, status="promoted")
        st.storage.upsert_parser(parser_id=p.id, promoted_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc), promoted_by=req.approved_by)
        st.storage.audit(tid, req.approved_by, "parser.promote", p.id, {"suggestion_id": suggestion_id})
        return {"parser_id": p.id, "version": p.version, "status": "active"}

    @app.get("/v1/drift")
    def drift(kind: str | None = None, tid: str = Depends(tenant_id)) -> list[dict]:
        rows = S().storage.list_drift(tid)
        return [r for r in rows if kind is None or r["kind"] == kind]

    @app.get("/v1/drift/parsers")
    def parser_drift(tid: str = Depends(tenant_id)) -> list[dict]:
        return S().storage.list_parser_drift(tid)

    @app.get("/v1/stats")
    def stats(tid: str = Depends(tenant_id)) -> dict:
        return S().storage.stats(tid)

    return app


app = create_app()


def main() -> None:
    import uvicorn

    s = app.state.settings
    uvicorn.run(app, host=s.api_host, port=s.api_port, log_config=None)


if __name__ == "__main__":
    main()
