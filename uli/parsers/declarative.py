"""Tier-2 declarative vendor parser packs (YAML). No code execution; regex is timeout-guarded.
Schema: schemas/vendors/pack.schema.json (validated on load)."""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any

import jsonschema
import regex
import yaml

from uli.fingerprint import tokenize
from uli.models import IR, ParseContext, TokenType
from uli.parsers.base import Parser, ParserKind, ParserMetadata, guarded_search
from uli.parsers.structural import STRUCTURAL_PARSERS, level_to_severity
from uli.timestamps import parse_any, parse_timestamp

_SCHEMA_CACHE: dict | None = None


def pack_schema(schemas_dir: Path) -> dict:
    global _SCHEMA_CACHE
    if _SCHEMA_CACHE is None:
        _SCHEMA_CACHE = json.loads((Path(schemas_dir) / "vendors" / "pack.schema.json").read_text())
    return _SCHEMA_CACHE


def validate_pack(spec: dict, schemas_dir: Path) -> list[str]:
    v = jsonschema.Draft202012Validator(pack_schema(schemas_dir))
    return [f"{'/'.join(str(p) for p in e.path)}: {e.message}" for e in v.iter_errors(spec)]


_STRUCT_BY_ID = {p.id: p for p in STRUCTURAL_PARSERS}


class DeclarativeParser(Parser):
    kind = ParserKind.DECLARATIVE
    tier = 2

    def __init__(self, spec: dict, regex_timeout_ms: int = 50, source_yaml: str | None = None, status: str = "active"):
        self.spec = spec
        self.id = spec["parser_id"]
        self.version = str(spec.get("version", "1.0.0"))
        self.vendor = spec.get("vendor")
        self.product = spec.get("product")
        self.status = status
        self.yaml = source_yaml
        self.timeout_ms = regex_timeout_ms
        self._sigs = spec.get("signatures", [])
        self._pre = spec.get("pre")  # optional structural parser to run first (e.g. syslog3164)
        self._extract = spec.get("extract", [])
        self._map = spec.get("map", {})
        self._conf = spec.get("confidence", {})
        self._compiled: dict[int, regex.Pattern] = {}
        for i, ex in enumerate(self._extract):
            if ex["kind"] == "regex":
                self._compiled[i] = regex.compile(ex["pattern"], regex.S)
            elif ex["kind"] == "grok_lite":
                self._compiled[i] = regex.compile(_grok_expand(ex["pattern"]), regex.S)
        for i, s in enumerate(self._sigs):
            if s.get("kind") == "regex":
                s["_re"] = regex.compile(s["pattern"])

    # ------------------------------------------------------------------ signatures
    def can_parse(self, ctx: ParseContext) -> float:
        best = 0.0
        text = ctx.text
        app = _syslog_app(ctx)
        for s in self._sigs:
            k = s.get("kind")
            score = 0.0
            if k == "syslog_app":
                if app and (app == s.get("equals") or (s.get("startswith") and app.startswith(s["startswith"]))):
                    score = 1.0
            elif k == "prefix":
                if text.startswith(s["startswith"]):
                    score = 0.95
            elif k == "contains":
                if s["value"] in text[: s.get("within", 512)]:
                    score = 0.85
            elif k == "regex":
                if guarded_search(s["_re"], text[: s.get("within", 1024)], self.timeout_ms):
                    score = 0.9
            elif k == "shape_family":
                if ctx.fingerprint.family_hash == s["equals"]:
                    score = 0.8
            elif k == "json_keys":
                if text.lstrip().startswith("{") and all(f'"{key}"' in text for key in s["all"]):
                    score = 0.9
            elif k == "token_count":
                if s.get("min", 0) <= ctx.fingerprint.token_count <= s.get("max", 10**6):
                    score = 0.5
            if score > best:
                best = score
        # "all" semantics: if any signature is marked required and fails → 0
        for s in self._sigs:
            if s.get("required") and not self._sig_ok(s, ctx, app):
                return 0.0
        return best * float(self._conf.get("base", 0.9)) if best else 0.0

    def _sig_ok(self, s: dict, ctx: ParseContext, app: str | None) -> bool:
        k = s.get("kind")
        t = ctx.text
        if k == "syslog_app":
            return bool(app and (app == s.get("equals") or (s.get("startswith") and app.startswith(s["startswith"]))))
        if k == "prefix":
            return t.startswith(s["startswith"])
        if k == "contains":
            return s["value"] in t[: s.get("within", 512)]
        if k == "regex":
            return bool(guarded_search(s["_re"], t[: s.get("within", 1024)], self.timeout_ms))
        if k == "shape_family":
            return ctx.fingerprint.family_hash == s["equals"]
        if k == "json_keys":
            return all(f'"{key}"' in t for key in s["all"])
        if k == "token_count":
            return s.get("min", 0) <= ctx.fingerprint.token_count <= s.get("max", 10**6)
        return True

    # ------------------------------------------------------------------ parse
    def parse(self, ctx: ParseContext) -> IR:
        ir = self.new_ir(ctx, float(self._conf.get("base", 0.9)))
        ir.class_uid = self._map.get("class_uid")
        ir.activity_id = self._map.get("activity_id")
        ir.product_version = self.spec.get("product_version")
        # optional structural pre-parse (e.g. peel syslog envelope)
        if self._pre and self._pre in _STRUCT_BY_ID:
            pre_ir = _STRUCT_BY_ID[self._pre].parse(ctx)
            ir.fields.update({k: v for k, v in pre_ir.fields.items() if v is not None})
            ir.timestamp = pre_ir.timestamp
            ir.severity = pre_ir.severity
            ir.message = pre_ir.message
            if pre_ir.errors:
                ir.errors.extend(pre_ir.errors)
        else:
            ir.message = ctx.text
        for i, ex in enumerate(self._extract):
            try:
                self._run_extract(i, ex, ctx, ir)
            except Exception as e:  # noqa: BLE001
                ir.errors.append(f"extract_{ex['kind']}_error:{type(e).__name__}")
        self._apply_map(ir)
        self._score(ir)
        return ir

    def _src(self, ex: dict, ctx: ParseContext, ir: IR) -> str:
        f = ex.get("field", "message")
        if f == "raw":
            return ctx.text
        v = ir.fields.get(f) if f != "message" else ir.message
        return "" if v is None else str(v)

    def _run_extract(self, i: int, ex: dict, ctx: ParseContext, ir: IR) -> None:
        kind = ex["kind"]
        src = self._src(ex, ctx, ir)
        if kind == "regex" or kind == "grok_lite":
            m = guarded_search(self._compiled[i], src, self.timeout_ms)
            if m is None:
                ir.errors.append(f"{kind}_nomatch" if not ex.get("optional") else f"{kind}_optional_nomatch")
                return
            for k, v in m.groupdict().items():
                if v is not None:
                    ir.fields[k] = v
        elif kind == "csv" or kind == "split":
            delim = ex.get("delimiter", "," if kind == "csv" else " ")
            if delim == "\\t":
                delim = "\t"
            if kind == "csv":
                row = next(csv.reader(io.StringIO(src), delimiter=delim, quotechar=ex.get("quote", '"')), [])
            else:
                row = src.split(delim) if delim != " " else src.split()
            cols = ex.get("columns", [])
            for k, v in zip(cols, row):
                if k and k != "_":
                    ir.fields[k] = v
            if ex.get("rest_field") and len(row) > len(cols):
                ir.fields[ex["rest_field"]] = delim.join(row[len(cols):])
            if len(row) < ex.get("min_columns", 0):
                ir.errors.append("csv_short_row")
        elif kind == "kv":
            sep = ex.get("sep", "=")
            pair_sep = ex.get("pair_sep", " ")
            for part in (src.split(pair_sep) if pair_sep != " " else src.split()):
                if sep in part:
                    k, v = part.split(sep, 1)
                    ir.fields[k.strip()] = v.strip().strip('"')
        elif kind == "json_path":
            try:
                obj = json.loads(src)
            except json.JSONDecodeError:
                ir.errors.append("json_path_invalid_json")
                return
            for name, path in ex["paths"].items():
                cur: Any = obj
                for p in path.split("."):
                    cur = cur.get(p) if isinstance(cur, dict) else None
                    if cur is None:
                        break
                if cur is not None:
                    ir.fields[name] = cur
        elif kind == "positional":
            toks = tokenize(src)
            for pos, name in ex["positions"].items():
                p = int(pos)
                if p < len(toks):
                    ir.fields[name] = toks[p]
        elif kind == "const":
            ir.fields.update(ex["values"])

    def _apply_map(self, ir: IR) -> None:
        for target, rule in (self._map.get("fields") or {}).items():
            try:
                if "const" in rule:
                    val = rule["const"]
                else:
                    val = ir.fields.get(rule["from"])
                    if val in (None, ""):
                        if "default" in rule:
                            val = rule["default"]
                        else:
                            continue
                    if rule.get("lower"):
                        val = str(val).lower()
                    if "enum" in rule:
                        val = rule["enum"].get(str(val).lower() if rule.get("lower") else str(val), rule.get("enum_default"))
                        if val is None:
                            continue
                    t = rule.get("type")
                    if t == "int":
                        val = int(float(val))
                    elif t == "float":
                        val = float(val)
                    elif t == "bool":
                        val = str(val).lower() in ("1", "true", "yes", "y")
                    elif t == "ts":
                        dt = parse_any(str(val)) or parse_timestamp(str(val))[0]
                        if dt is None:
                            continue
                        val = dt
                    elif t == "severity":
                        val = level_to_severity(val)
                        if val is None:
                            continue
                ir.fields[f"ocsf.{target}"] = val
                if target == "time" and hasattr(val, "timestamp"):
                    ir.timestamp = val
                if target == "severity_id" and isinstance(val, int):
                    ir.severity = val
            except (ValueError, TypeError) as e:
                ir.errors.append(f"map_{target}_error:{type(e).__name__}")
        if self._map.get("message_from") and ir.fields.get(self._map["message_from"]):
            ir.message = str(ir.fields[self._map["message_from"]])

    def _score(self, ir: IR) -> None:
        req = self._conf.get("require", [])
        missing = [r for r in req if ir.fields.get(r) in (None, "")]
        mapped = [k for k in ir.fields if k.startswith("ocsf.")]
        total_map = len(self._map.get("fields") or {}) or 1
        fill = min(1.0, len(mapped) / total_map)
        factors = {"base": float(self._conf.get("base", 0.9)), "fill": fill ** 0.5, "require": 0.5 if missing else 1.0, "ts": 1.0 if ir.timestamp else 0.85}
        conf = 1.0
        for v in factors.values():
            conf *= v
        if any(e.endswith("_nomatch") for e in ir.errors):
            factors["nomatch"] = 0.5
            conf *= 0.5
        ir.confidence = max(0.0, min(1.0, conf))
        ir.confidence_factors = factors
        if missing:
            ir.errors.append("missing_required:" + ",".join(missing))

    def metadata(self) -> ParserMetadata:
        return ParserMetadata(parser_id=self.id, version=self.version, kind=self.kind, tier=self.tier, vendor=self.vendor, product=self.product,
                              signatures=[{k: v for k, v in s.items() if not k.startswith("_")} for s in self._sigs],
                              compat=self.spec.get("compat", {"envelope": "uli.v1", "ocsf": ">=1.6 <2"}), description=self.spec.get("description", ""), status=self.status)


def _syslog_app(ctx: ParseContext) -> str | None:
    """Cheap syslog appname extraction for signature matching (no full parse)."""
    t = ctx.text
    h = ctx.hints.get("syslog_app")
    if h:
        return h
    m = regex.match(r"^(?:<\d{1,3}>)?(?:1 \S+ \S+ (?P<app5>\S+)|[A-Z][a-z]{2}\s{1,2}\d{1,2} \d{2}:\d{2}:\d{2}\S* \S+ (?P<app3>[^\s:\[\(]+))", t[:160])
    if m:
        return m["app5"] or m["app3"]
    return None


_GROK_LITE = {
    "IP": r"(?:\d{1,3}\.){3}\d{1,3}", "IPV6": r"[0-9a-fA-F:]{3,39}", "INT": r"[+-]?\d+", "NUMBER": r"[+-]?\d+(?:\.\d+)?",
    "WORD": r"\w+", "NOTSPACE": r"\S+", "DATA": r".*?", "GREEDYDATA": r".*", "HOSTNAME": r"[\w.-]+", "USER": r"[\w.@-]+",
    "TIMESTAMP_ISO8601": r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?",
    "SYSLOGTIMESTAMP": r"[A-Z][a-z]{2}\s{1,2}\d{1,2} \d{2}:\d{2}:\d{2}", "LOGLEVEL": r"(?:TRACE|DEBUG|INFO|NOTICE|WARN(?:ING)?|ERR(?:OR)?|CRIT(?:ICAL)?|FATAL|ALERT|EMERG(?:ENCY)?)",
    "MAC": r"(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}", "PATH": r"(?:/[\w.-]+)+", "URI": r"\w+://\S+", "QS": r'"(?:\\"|[^"])*"',
}


def _grok_expand(pattern: str) -> str:
    def rep(m: regex.Match) -> str:
        name, field = m.group(1), m.group(2)
        body = _GROK_LITE.get(name, r"\S+")
        return f"(?P<{field}>{body})" if field else f"(?:{body})"
    return regex.sub(r"%\{(\w+)(?::(\w+))?\}", rep, pattern)


def load_pack_yaml(text: str, schemas_dir: Path, regex_timeout_ms: int = 50, status: str = "active") -> DeclarativeParser:
    spec = yaml.safe_load(text)
    if not isinstance(spec, dict):
        raise ValueError("pack must be a mapping")
    errs = validate_pack(spec, schemas_dir)
    if errs:
        raise ValueError("pack schema errors: " + "; ".join(errs[:5]))
    return DeclarativeParser(spec, regex_timeout_ms=regex_timeout_ms, source_yaml=text, status=status)
