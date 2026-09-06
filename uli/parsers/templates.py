"""Tier-4: Drain3 template mining, one miner per (tenant, source), with masking driven by our own
token classes and state persisted to disk (or Redis when configured)."""
from __future__ import annotations

import logging
import threading
from pathlib import Path

from drain3 import TemplateMiner
from drain3.file_persistence import FilePersistence
from drain3.template_miner_config import TemplateMinerConfig

from uli.models import IR, ParseContext, TokenType

logging.getLogger("drain3").setLevel(logging.WARNING)

_MASK = {TokenType.IP4: "<IP>", TokenType.IP6: "<IP>", TokenType.NUM: "<NUM>", TokenType.HEX: "<HEX>", TokenType.TS: "<TS>",
         TokenType.UUID: "<UUID>", TokenType.URL: "<URL>", TokenType.EMAIL: "<EMAIL>", TokenType.MAC: "<MAC>", TokenType.PATH: "<PATH>"}


def masked_text(ctx: ParseContext, message: str | None = None) -> tuple[str, list[str]]:
    """Replace typed tokens with class masks before Drain sees them (improves grouping stability
    under value churn). Returns (masked, params_in_order)."""
    fp = ctx.fingerprint
    src_tokens = fp.tokens
    if message is not None and message != ctx.text:
        from uli.fingerprint import fingerprint as _fp  # local to avoid cycle
        f2 = _fp(message)
        src_tokens, classes = f2.tokens, f2.classes
    else:
        classes = fp.classes
    out: list[str] = []
    params: list[str] = []
    for tok, cls in zip(src_tokens, classes):
        m = _MASK.get(cls)
        if m:
            out.append(m)
            params.append(tok)
        else:
            out.append(tok)
    return " ".join(out), params


class TemplateMinerPool:
    def __init__(self, state_dir: Path, sim_th: float = 0.4, depth: int = 4, max_clusters: int = 5000):
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.sim_th, self.depth, self.max_clusters = sim_th, depth, max_clusters
        self._miners: dict[tuple[str, str], TemplateMiner] = {}
        self._lock = threading.Lock()

    def _key_path(self, tenant: str, source: str) -> Path:
        safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in f"{tenant}__{source}")[:150]
        return self.state_dir / f"{safe}.bin"

    def get(self, tenant: str, source: str) -> TemplateMiner:
        key = (tenant, source)
        with self._lock:
            m = self._miners.get(key)
            if m is None:
                cfg = TemplateMinerConfig()
                cfg.drain_sim_th = self.sim_th
                cfg.drain_depth = self.depth
                cfg.drain_max_clusters = self.max_clusters
                cfg.drain_extra_delimiters = ["=", ":", ",", "|"]
                cfg.mask_prefix, cfg.mask_suffix = "<", ">"
                cfg.masking_instructions = []  # we mask ourselves
                cfg.snapshot_interval_minutes = 1
                cfg.snapshot_compress_state = True
                m = TemplateMiner(FilePersistence(str(self._key_path(tenant, source))), cfg)
                self._miners[key] = m
        return m

    def mine(self, ctx: ParseContext, ir: IR, message: str | None = None) -> IR:
        """Total function: on any failure, IR is returned unchanged with an error noted."""
        try:
            masked, params = masked_text(ctx, message)
            miner = self.get(ctx.tenant_id, ctx.source_id)
            res = miner.add_log_message(masked)
            ir.template_id = f"drain:{res['cluster_id']}"
            ir.template = res["template_mined"]
            ir.params = params + _wild_params(res["template_mined"], masked)
            ir.fields["__template_change"] = res.get("change_type", "none")
            ir.fields["__cluster_size"] = res.get("cluster_size", 1)
        except Exception as e:  # noqa: BLE001
            ir.errors.append(f"template_mining_error:{type(e).__name__}")
        return ir

    def templates(self, tenant: str, source: str) -> list[dict]:
        m = self.get(tenant, source)
        return [{"cluster_id": c.cluster_id, "size": c.size, "template": c.get_template()} for c in m.drain.clusters]

    def save_all(self) -> None:
        with self._lock:
            for m in self._miners.values():
                try:
                    m.save_state("shutdown")
                except Exception:
                    pass


def _wild_params(template: str, masked: str) -> list[str]:
    """Extract tokens that Drain generalised to <*> (beyond our own masks)."""
    tt, mt = template.split(), masked.split()
    if len(tt) != len(mt):
        return []
    return [m for t, m in zip(tt, mt) if t == "<*>" and m != t]
