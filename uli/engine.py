"""The parsing ladder orchestrator (docs/parser-design.md §2). This is the one place that
guarantees P1 (total pipeline): whatever happens inside, `run()` always returns an IR."""
from __future__ import annotations

import time
from pathlib import Path
from threading import Lock
from typing import Any

from uli.config import Settings
from uli.detection.similarity import SimilarityIndex
from uli.fingerprint import fingerprint
from uli.logging import get_logger
from uli.metrics import ML_FAILURES, ML_LATENCY, PARSER_CONFIDENCE, ROUTING_CACHE
from uli.models import IR, ParseContext
from uli.parsers.base import Parser
from uli.parsers.declarative import load_pack_yaml
from uli.parsers.inference import infer
from uli.parsers.structural import STRUCTURAL_PARSERS
from uli.parsers.templates import TemplateMinerPool

log = get_logger("engine")

_MAX_TEXT_DECODE_ERRORS_LOGGED = 50


class MLClient:
    """Optional sidecar client with a circuit breaker. Never blocks the pipeline (P4)."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._failures = 0
        self._open_until = 0.0
        self._client = None

    def _get_client(self):
        if self._client is None:
            import httpx

            self._client = httpx.Client(base_url=self.settings.ml_url, timeout=self.settings.ml_timeout_ms / 1000.0)
        return self._client

    def available(self) -> bool:
        return self.settings.ml_enabled and time.monotonic() >= self._open_until

    def classify_and_score(self, shape: str, tokens: list[str], template: str | None) -> dict[str, Any]:
        if not self.available():
            return {}
        t0 = time.monotonic()
        try:
            r = self._get_client().post("/v1/infer", json={"shape": shape, "tokens": tokens[:64], "template": template})
            r.raise_for_status()
            self._failures = 0
            ML_LATENCY.observe(time.monotonic() - t0)
            return r.json()
        except Exception as e:  # noqa: BLE001
            ML_FAILURES.labels(reason=type(e).__name__).inc()
            self._failures += 1
            if self._failures >= self.settings.ml_circuit_failures:
                self._open_until = time.monotonic() + self.settings.ml_circuit_reset_s
                log.warning("ml_circuit_open", reset_s=self.settings.ml_circuit_reset_s)
            return {}


class ParserEngine:
    def __init__(self, settings: Settings, storage, template_pool: TemplateMinerPool | None = None):
        self.settings = settings
        self.storage = storage
        self.structural: list[Parser] = list(STRUCTURAL_PARSERS)
        self.declarative: dict[str, Parser] = {}
        self.templates = template_pool or TemplateMinerPool(settings.drain_dir, settings.drain_sim_th, settings.drain_depth, settings.drain_max_clusters)
        self.similarity = SimilarityIndex.empty()
        self.ml = MLClient(settings)
        self._routing_cache: dict[str, str] = {}
        self._lock = Lock()
        self.load_parsers_dir(settings.parsers_dir / "vendors")

    # ------------------------------------------------------------- registry
    def load_parsers_dir(self, path: Path) -> list[str]:
        errors: list[str] = []
        if not path.exists():
            return errors
        for f in sorted(path.glob("*.yaml")) + sorted(path.glob("*.yml")):
            try:
                self.register_pack(f.read_text(encoding="utf-8"), origin=str(f))
            except Exception as e:  # noqa: BLE001
                errors.append(f"{f.name}: {e}")
                log.error("pack_load_failed", file=str(f), error=str(e)[:300])
        return errors

    def register_pack(self, yaml_text: str, *, origin: str = "api", status: str = "active") -> Parser:
        p = load_pack_yaml(yaml_text, self.settings.schemas_dir, self.settings.regex_timeout_ms, status=status)
        with self._lock:
            self.declarative[p.id] = p
            self._routing_cache.clear()  # signatures may overlap differently now
        if self.storage:
            self.storage.upsert_parser(parser_id=p.id, version=p.version, kind=p.kind.value, vendor=p.vendor, product=p.product,
                                       signatures=[{k: v for k, v in s.items() if not k.startswith("_")} for s in p._sigs],
                                       compat=p.spec.get("compat", {}), status=status, yaml=yaml_text, bundle_sha256=None, signature_ok=True)
            self.storage.audit("*", "system", "parser.load", p.id, {"origin": origin, "version": p.version})
        log.info("parser_registered", parser_id=p.id, version=p.version, origin=origin)
        return p

    def retire_pack(self, parser_id: str) -> bool:
        with self._lock:
            existed = self.declarative.pop(parser_id, None) is not None
            self._routing_cache = {k: v for k, v in self._routing_cache.items() if v != parser_id}
        if existed and self.storage:
            self.storage.upsert_parser(parser_id=parser_id, status="retired")
        return existed

    # ------------------------------------------------------------- run
    def run(self, *, tenant_id: str, source_id: str, transport: str, text: str, raw: bytes, hints: dict[str, Any] | None = None) -> tuple[ParseContext, IR]:
        t0 = time.monotonic()
        truncated = False
        if len(text.encode("utf-8", "replace")) > self.settings.max_line_bytes:
            text = text.encode("utf-8", "replace")[: self.settings.max_line_bytes].decode("utf-8", "ignore")
            truncated = True
        try:
            fp = fingerprint(text)
        except Exception as e:  # noqa: BLE001 — fingerprinting itself must never abort the pipeline
            log.error("fingerprint_failed", error=str(e)[:200])
            fp = fingerprint("")
        ctx = ParseContext(raw=raw, text=text, fingerprint=fp, tenant_id=tenant_id, source_id=source_id, transport=transport, hints=hints or {}, truncated=truncated)
        ir = self._route(ctx)
        try:
            infer(ctx, ir)
        except Exception as e:  # noqa: BLE001
            ir.errors.append(f"infer_wrapper_error:{type(e).__name__}")
        if ir.confidence < self.settings.tau_known:
            self._fallback(ctx, ir)
        PARSER_CONFIDENCE.labels(parser_id=ir.parser_id).observe(ir.confidence)
        ir.fields.setdefault("__latency_ms", round((time.monotonic() - t0) * 1000, 3))
        return ctx, ir

    def _route(self, ctx: ParseContext) -> IR:
        cached = self._routing_cache.get(ctx.fingerprint.shape_hash)
        candidates: list[Parser] = []
        if cached:
            p = self.declarative.get(cached) or next((s for s in self.structural if s.id == cached), None)
            if p is not None:
                ROUTING_CACHE.labels("hit").inc()
                candidates = [p]
        if not candidates:
            ROUTING_CACHE.labels("miss").inc()
            candidates = list(self.declarative.values()) + self.structural
        best_parser: Parser | None = None
        best_score = 0.0
        for p in candidates:
            try:
                score = p.can_parse(ctx)
            except Exception as e:  # noqa: BLE001
                log.warning("can_parse_error", parser_id=getattr(p, "id", "?"), error=str(e)[:200])
                score = 0.0
            if score > best_score:
                best_parser, best_score = p, score
        if best_parser is None or best_score <= 0.0:
            ir = IR(message=ctx.text, tier=7, confidence=0.0, parser_id="none", parser_version="0")
            ir.errors.append("no_tier1_2_match")
            return ir
        try:
            ir = best_parser.parse(ctx)
        except Exception as e:  # noqa: BLE001 — a misbehaving parser must not kill the event
            log.error("parser_raised", parser_id=best_parser.id, error=str(e)[:300])
            ir = IR(message=ctx.text, tier=7, confidence=0.0, parser_id=best_parser.id, parser_version=getattr(best_parser, "version", "0"))
            ir.errors.append(f"parser_exception:{type(e).__name__}")
            return ir
        if ir.confidence >= self.settings.tau_known:
            self._routing_cache[ctx.fingerprint.shape_hash] = best_parser.id
            if self.storage:
                self.storage.upsert_fingerprint(shape_hash=ctx.fingerprint.shape_hash, family_hash=ctx.fingerprint.family_hash, shape=ctx.fingerprint.shape, routed_parser_id=best_parser.id)
            self.similarity.add_reference(best_parser.id, ctx.fingerprint.shape)
        return ir

    def _fallback(self, ctx: ParseContext, ir: IR) -> None:
        # Tier 4: Drain3 template mining (always attempted; enriches even a tier-1/2 partial hit)
        self.templates.mine(ctx, ir, message=ir.message or ctx.text)
        if ir.confidence < self.settings.tau_known and ir.tier > 4:
            ir.tier = 4
            ir.confidence = max(ir.confidence, 0.5 if "template_mining_error" not in " ".join(ir.errors) else 0.2)
            ir.parser_id, ir.parser_version = "engine.drain3", "1.0.0"
        # Tier 5: similarity to known parser shapes
        if ir.confidence < self.settings.tau_known:
            match = self.similarity.best_match(ctx.fingerprint.shape)
            if match and match[1] >= self.settings.similarity_threshold:
                pid, sim = match
                p = self.declarative.get(pid)
                if p is not None:
                    try:
                        candidate_ir = p.parse(ctx)
                        candidate_ir.confidence *= sim
                        candidate_ir.tier = 5
                        candidate_ir.candidates.append({"parser_id": pid, "similarity": sim})
                        if candidate_ir.confidence > ir.confidence:
                            ir.__dict__.update(candidate_ir.__dict__)
                    except Exception as e:  # noqa: BLE001
                        ir.errors.append(f"similarity_candidate_error:{type(e).__name__}")
        # Tier 6: optional ML sidecar (annotate only, never overrides parsing decision)
        ml_out = self.ml.classify_and_score(ctx.fingerprint.shape, ctx.fingerprint.tokens, ir.template)
        if ml_out:
            ir.ml.update(ml_out)
            if ml_out.get("vendor_guess") and ir.confidence < self.settings.tau_known:
                ir.fields.setdefault("__ml_vendor_guess", ml_out["vendor_guess"])
        # Tier 7: quarantine (handled by caller via unknown registry; here we just tag it)
        if ir.confidence < self.settings.tau_known:
            ir.tier = 7 if ir.confidence < 0.4 else ir.tier
            ir.parser_id = ir.parser_id if ir.parser_id != "none" else "engine.quarantine"
