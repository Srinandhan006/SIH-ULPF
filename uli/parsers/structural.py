"""Tier-1 structural parsers: JSON, CEF, LEEF, syslog RFC5424/3164, logfmt/KV, CLF (Apache/Nginx
access), Apache error log, OTLP-JSON, Docker json-file. Vendor-independent by definition."""
from __future__ import annotations

import json
import re
from datetime import timezone
from typing import Any

import orjson

from uli.models import IR, ParseContext, TokenType
from uli.parsers.base import Parser, ParserKind
from uli.timestamps import parse_any, parse_timestamp

_SEV_SYSLOG = {0: 1, 1: 6, 2: 5, 3: 4, 4: 3, 5: 2, 6: 1, 7: 1}  # syslog severity → OCSF severity_id (rough)
_LEVEL_WORDS = {
    "emerg": 6, "emergency": 6, "panic": 6, "fatal": 6, "alert": 5, "crit": 5, "critical": 5, "severe": 5,
    "error": 4, "err": 4, "warn": 3, "warning": 3, "notice": 2, "info": 1, "informational": 1, "debug": 1, "trace": 1, "fine": 1,
}


def _flatten(obj: Any, prefix: str = "", out: dict[str, Any] | None = None, depth: int = 0, max_depth: int = 8, max_keys: int = 500) -> dict[str, Any]:
    if out is None:
        out = {}
    if len(out) >= max_keys:
        return out
    if isinstance(obj, dict) and depth < max_depth:
        for k, v in obj.items():
            _flatten(v, f"{prefix}{k}." if prefix or True else k, out, depth + 1, max_depth, max_keys) if isinstance(v, (dict, list)) else out.__setitem__(f"{prefix}{k}", v)
    elif isinstance(obj, list) and depth < max_depth:
        if all(not isinstance(x, (dict, list)) for x in obj):
            out[prefix.rstrip(".")] = obj
        else:
            for i, v in enumerate(obj[:50]):
                _flatten(v, f"{prefix}{i}.", out, depth + 1, max_depth, max_keys)
    else:
        out[prefix.rstrip(".")] = obj if not isinstance(obj, (dict, list)) else json.dumps(obj)[:2000]
    return out


def level_to_severity(v: Any) -> int | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        i = int(v)
        return i if 0 <= i <= 6 else None
    return _LEVEL_WORDS.get(str(v).strip().lower())


# --------------------------------------------------------------------------- JSON
class JSONParser(Parser):
    id = "structural.json"
    _TS_KEYS = ("timestamp", "@timestamp", "time", "ts", "eventTime", "event_time", "date", "datetime", "Timestamp", "TimeGenerated", "_time", "log_time", "logged_at")
    _MSG_KEYS = ("message", "msg", "log", "body", "text", "event", "Message", "description")
    _SEV_KEYS = ("severity", "level", "loglevel", "log_level", "levelname", "sev", "priority", "Severity", "level_name")

    def can_parse(self, ctx: ParseContext) -> float:
        t = ctx.text.lstrip()
        if t.startswith("{") and t.rstrip().endswith("}"):
            return 0.9
        return 0.0

    def parse(self, ctx: ParseContext) -> IR:
        ir = self.new_ir(ctx, 0.75)
        try:
            obj = orjson.loads(ctx.text)
        except Exception as e:  # noqa: BLE001
            ir.errors.append(f"json_invalid:{type(e).__name__}")
            ir.confidence = 0.0
            return ir
        if not isinstance(obj, dict):
            ir.fields["value"] = obj
            ir.confidence = 0.5
            return ir
        # Docker json-file / OTLP / generic
        if set(obj) >= {"log", "stream", "time"} and isinstance(obj.get("log"), str):
            ir.product = "docker-json-file"
            ir.fields.update(_flatten(obj))
            ir.message = obj["log"].rstrip("\n")
            ir.timestamp = parse_any(str(obj["time"]))
            ir.fields["container.stream"] = obj["stream"]
            ir.confidence = 0.85
            return ir
        if "resourceLogs" in obj or ("body" in obj and "attributes" in obj):
            ir.product = "otlp-json"
        flat = _flatten(obj)
        ir.fields.update(flat)
        for k in self._TS_KEYS:
            if k in obj and obj[k] not in (None, ""):
                ir.timestamp = parse_any(str(obj[k])) if not isinstance(obj[k], (int, float)) else parse_timestamp(str(obj[k]))[0]
                if ir.timestamp:
                    ir.fields["__ts_field"] = k
                    break
        for k in self._MSG_KEYS:
            if isinstance(obj.get(k), str):
                ir.message = obj[k]
                break
        for k in self._SEV_KEYS:
            if k in obj:
                ir.severity = level_to_severity(obj[k])
                break
        ir.confidence = 0.8 if ir.timestamp else 0.7
        return ir


# --------------------------------------------------------------------------- CEF
class CEFParser(Parser):
    id = "structural.cef"
    _HDR = re.compile(r"CEF:(?P<v>\d+)\|(?P<vendor>(?:\\\||[^|])*)\|(?P<product>(?:\\\||[^|])*)\|(?P<version>(?:\\\||[^|])*)\|(?P<sig>(?:\\\||[^|])*)\|(?P<name>(?:\\\||[^|])*)\|(?P<sev>(?:\\\||[^|])*)\|(?P<ext>.*)$", re.S)
    _EXT = re.compile(r"(?P<k>[A-Za-z0-9_.]+)=(?P<v>(?:\\=|[^=])*?)(?=\s+[A-Za-z0-9_.]+=|$)")

    def can_parse(self, ctx: ParseContext) -> float:
        return 0.95 if "CEF:" in ctx.text[:160] else 0.0

    def parse(self, ctx: ParseContext) -> IR:
        ir = self.new_ir(ctx, 0.85)
        idx = ctx.text.find("CEF:")
        prefix = ctx.text[:idx]
        m = self._HDR.match(ctx.text[idx:])
        if not m:
            ir.errors.append("cef_header_mismatch")
            ir.confidence = 0.3
            return ir
        un = lambda s: s.replace("\\|", "|").replace("\\\\", "\\")  # noqa: E731
        ir.vendor, ir.product, ir.product_version = un(m["vendor"]), un(m["product"]), un(m["version"])
        ir.fields.update({"cef.version": m["v"], "cef.signature_id": un(m["sig"]), "cef.name": un(m["name"]), "cef.severity": un(m["sev"])})
        try:
            s = int(m["sev"])
            ir.severity = 1 if s <= 3 else 2 if s <= 5 else 3 if s <= 6 else 4 if s <= 8 else 5
        except ValueError:
            ir.severity = level_to_severity(m["sev"])
        for em in self._EXT.finditer(m["ext"].strip()):
            ir.fields[em["k"]] = em["v"].replace("\\=", "=").replace("\\n", "\n").replace("\\\\", "\\")
        ir.message = ir.fields.get("msg") or un(m["name"])
        if prefix.strip():
            ts, _, _ = parse_timestamp(prefix)
            ir.timestamp = ts
            ir.fields["syslog.prefix"] = prefix.strip()
        for k in ("rt", "end", "start"):
            if k in ir.fields and ir.timestamp is None:
                ir.timestamp = parse_any(ir.fields[k]) or parse_timestamp(ir.fields[k])[0]
        return ir


# --------------------------------------------------------------------------- LEEF
class LEEFParser(Parser):
    id = "structural.leef"
    _HDR = re.compile(r"LEEF:(?P<v>[\d.]+)\|(?P<vendor>[^|]*)\|(?P<product>[^|]*)\|(?P<version>[^|]*)\|(?P<eid>[^|]*)\|(?:(?P<delim>[^|]*)\|)?(?P<attrs>.*)$", re.S)

    def can_parse(self, ctx: ParseContext) -> float:
        return 0.95 if "LEEF:" in ctx.text[:160] else 0.0

    def parse(self, ctx: ParseContext) -> IR:
        ir = self.new_ir(ctx, 0.85)
        idx = ctx.text.find("LEEF:")
        m = self._HDR.match(ctx.text[idx:])
        if not m:
            ir.errors.append("leef_header_mismatch")
            ir.confidence = 0.3
            return ir
        ir.vendor, ir.product, ir.product_version = m["vendor"], m["product"], m["version"]
        ir.fields["leef.event_id"] = m["eid"]
        delim = "\t"
        if m["v"].startswith("2") and m["delim"]:
            d = m["delim"]
            if d.startswith("x") or d.startswith("0x"):
                try:
                    delim = chr(int(d.lstrip("0x"), 16))
                except ValueError:
                    delim = d
            else:
                delim = d
        attrs = m["attrs"]
        if delim not in attrs and "\t" not in attrs:
            delim = " "
        for part in attrs.split(delim):
            if "=" in part:
                k, v = part.split("=", 1)
                ir.fields[k.strip()] = v.strip()
        ir.message = ir.fields.get("msg") or m["eid"]
        prefix = ctx.text[:idx]
        if prefix.strip():
            ir.timestamp = parse_timestamp(prefix)[0]
        if ir.timestamp is None and "devTime" in ir.fields:
            ir.timestamp = parse_any(ir.fields["devTime"])
        return ir


# --------------------------------------------------------------------------- syslog
class Syslog5424Parser(Parser):
    id = "structural.syslog5424"
    _RE = re.compile(r"^(?:<(?P<pri>\d{1,3})>)?1 (?P<ts>\S+) (?P<host>\S+) (?P<app>\S+) (?P<procid>\S+) (?P<msgid>\S+) (?P<sd>-|(?:\[.*?\])+)\s?(?P<msg>.*)$", re.S)
    _SD = re.compile(r'\[(?P<id>[^\s\]]+)((?:\s+[^=\s\]]+="(?:\\"|[^"])*")*)\]')
    _SDP = re.compile(r'([^=\s\]]+)="((?:\\"|[^"])*)"')

    def can_parse(self, ctx: ParseContext) -> float:
        t = ctx.text
        if t.startswith("<") and ">1 " in t[:8]:
            return 0.95
        if t.startswith("1 ") and len(ctx.fingerprint.classes) > 1 and ctx.fingerprint.classes[1] == TokenType.TS:
            return 0.8
        return 0.0

    def parse(self, ctx: ParseContext) -> IR:
        ir = self.new_ir(ctx, 0.8)
        m = self._RE.match(ctx.text)
        if not m:
            ir.errors.append("syslog5424_mismatch")
            ir.confidence = 0.3
            return ir
        if m["pri"]:
            pri = int(m["pri"])
            ir.fields["syslog.facility"], ir.fields["syslog.severity"] = pri >> 3, pri & 7
            ir.severity = _SEV_SYSLOG.get(pri & 7)
        ir.fields.update({"syslog.hostname": m["host"], "syslog.appname": m["app"], "syslog.procid": m["procid"], "syslog.msgid": m["msgid"]})
        for k in ("syslog.appname", "syslog.procid", "syslog.msgid"):
            if ir.fields[k] == "-":
                ir.fields[k] = None
        if m["ts"] != "-":
            ir.timestamp = parse_any(m["ts"])
        if m["sd"] != "-":
            for sm in self._SD.finditer(m["sd"]):
                for pk, pv in self._SDP.findall(sm.group(2)):
                    ir.fields[f"sd.{sm['id']}.{pk}"] = pv.replace('\\"', '"')
        ir.message = m["msg"]
        return ir


class Syslog3164Parser(Parser):
    id = "structural.syslog3164"
    _RE = re.compile(
        r"^(?:<(?P<pri>\d{1,3})>)?(?P<ts>[A-Z][a-z]{2}\s{1,2}\d{1,2} \d{2}:\d{2}:\d{2}(?:\.\d+)?(?: \d{4})?)\s+(?P<host>\S+)\s+"
        r"(?:(?P<app>[^\s:\[\(]+)(?:\((?P<mod>[^)]*)\))?(?:\[(?P<pid>[^\]]*)\])?:\s*)?(?P<msg>.*)$", re.S)
    _RE_ISO = re.compile(r"^(?:<(?P<pri>\d{1,3})>)?(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)\s+(?P<host>\S+)\s+(?:(?P<app>[^\s:\[]+)(?:\[(?P<pid>[^\]]*)\])?:\s*)?(?P<msg>.*)$", re.S)

    _HEAD_BSD = re.compile(r"^[A-Z][a-z]{2}\s{1,2}\d{1,2} \d{2}:\d{2}:\d{2}\S* \S+ [^\s:\[\(]+(?:\([^)]*\))?(?:\[\d+\])?:")
    _HEAD_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T\S+ \S+ [^\s:\[]+(?:\[\d+\])?:")
    _HEAD_BSD_LOOSE = re.compile(r"^[A-Z][a-z]{2}\s{1,2}\d{1,2} \d{2}:\d{2}:\d{2}")
    _HEAD_ISO_LOOSE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")

    def can_parse(self, ctx: ParseContext) -> float:
        t = ctx.text
        head = t[:160]
        if t.startswith("<") and t[1:4].rstrip(">").isdigit():
            return 0.85
        # Note: the fingerprint tokenizer splits "HH:MM:SS" on ':' into separate NUM tokens, so
        # fp.classes[0] is unreliable for detecting a leading timestamp here — match on text directly.
        if self._HEAD_BSD.match(head):
            return 0.8
        if self._HEAD_ISO.match(head):
            return 0.75
        if self._HEAD_BSD_LOOSE.match(head) or self._HEAD_ISO_LOOSE.match(head):
            return 0.4
        return 0.0

    def parse(self, ctx: ParseContext) -> IR:
        ir = self.new_ir(ctx, 0.7)
        m = self._RE.match(ctx.text) or self._RE_ISO.match(ctx.text)
        if not m:
            ir.errors.append("syslog3164_mismatch")
            ir.confidence = 0.3
            return ir
        gd = m.groupdict()
        if gd.get("pri"):
            pri = int(gd["pri"])
            ir.fields["syslog.facility"], ir.fields["syslog.severity"] = pri >> 3, pri & 7
            ir.severity = _SEV_SYSLOG.get(pri & 7)
        ir.timestamp = parse_timestamp(gd["ts"])[0]
        ir.fields["syslog.hostname"] = gd["host"]
        if gd.get("app"):
            ir.fields["syslog.appname"] = gd["app"]
        if gd.get("mod"):
            ir.fields["syslog.module"] = gd["mod"]
        if gd.get("pid"):
            ir.fields["syslog.procid"] = gd["pid"]
        ir.message = gd["msg"] or ""
        ir.confidence = 0.75 if gd.get("app") else 0.6
        return ir


# --------------------------------------------------------------------------- logfmt / KV
class KVParser(Parser):
    id = "structural.kv"
    _KV = re.compile(r'(?P<k>[A-Za-z_][\w.\-]*)=(?P<v>"(?:\\"|[^"])*"|\'[^\']*\'|\[[^\]]*\]|[^\s,;]+)')

    def can_parse(self, ctx: ParseContext) -> float:
        n = sum(1 for c in ctx.fingerprint.classes if c == TokenType.KV)
        if n >= 3:
            return 0.7
        if n >= 2 and ctx.fingerprint.token_count <= 12:
            return 0.55
        return 0.0

    def parse(self, ctx: ParseContext) -> IR:
        ir = self.new_ir(ctx, 0.6)
        n = 0
        for m in self._KV.finditer(ctx.text):
            v = m["v"]
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                v = v[1:-1]
            ir.fields[m["k"]] = v
            n += 1
        if n == 0:
            ir.confidence = 0.2
            ir.errors.append("kv_none")
        for k in ("time", "ts", "timestamp", "date", "eventtime", "devTime"):
            if k in ir.fields:
                ir.timestamp = parse_any(str(ir.fields[k]))
                if ir.timestamp:
                    break
        if ir.timestamp is None:
            ir.timestamp = parse_timestamp(ctx.text)[0]
        for k in ("level", "severity", "lvl", "sev"):
            if k in ir.fields:
                ir.severity = level_to_severity(ir.fields[k])
                break
        ir.message = ir.fields.get("msg") or ir.fields.get("message") or ctx.text
        return ir


# --------------------------------------------------------------------------- CLF (Apache/Nginx access)
class CLFParser(Parser):
    id = "structural.clf"
    _RE = re.compile(r'^(?P<ip>\S+) (?P<ident>\S+) (?P<user>\S+) \[(?P<ts>[^\]]+)\] "(?P<method>[A-Z]+) (?P<path>\S+)(?: (?P<proto>HTTP/[\d.]+))?" (?P<status>\d{3}) (?P<bytes>\d+|-)(?: "(?P<ref>[^"]*)" "(?P<ua>[^"]*)")?')

    def can_parse(self, ctx: ParseContext) -> float:
        t = ctx.text
        if ' "' in t and ("HTTP/" in t) and t.find("[") < 80 and t.find("[") > 0:
            return 0.85
        return 0.0

    def parse(self, ctx: ParseContext) -> IR:
        ir = self.new_ir(ctx, 0.85)
        m = self._RE.match(ctx.text)
        if not m:
            ir.errors.append("clf_mismatch")
            ir.confidence = 0.3
            return ir
        d = m.groupdict()
        ir.fields.update({"client.ip": d["ip"], "user": d["user"] if d["user"] != "-" else None, "http.method": d["method"], "url.path": d["path"], "http.version": d["proto"], "http.status": int(d["status"]), "http.bytes": int(d["bytes"]) if d["bytes"] != "-" else 0, "http.referrer": d["ref"], "http.user_agent": d["ua"]})
        ir.timestamp = parse_timestamp(d["ts"])[0]
        ir.message = f'{d["method"]} {d["path"]} {d["status"]}'
        ir.product = "http-server"
        ir.class_uid = 4002
        return ir


# --------------------------------------------------------------------------- Apache error log
class ApacheErrorParser(Parser):
    id = "structural.apache_error"
    _RE = re.compile(r"^\[(?P<ts>[A-Z][a-z]{2} [A-Z][a-z]{2} \d{1,2} \d{2}:\d{2}:\d{2}(?:\.\d+)? \d{4})\] \[(?P<lvl>[^\]]+)\](?: \[pid (?P<pid>\d+)(?::tid \d+)?\])?(?: \[client (?P<client>[^\]]+)\])? (?P<msg>.*)$", re.S)

    def can_parse(self, ctx: ParseContext) -> float:
        t = ctx.text
        return 0.85 if t.startswith("[") and re.match(r"^\[[A-Z][a-z]{2} [A-Z][a-z]{2} \d{1,2} \d{2}:\d{2}:\d{2}", t) else 0.0

    def parse(self, ctx: ParseContext) -> IR:
        ir = self.new_ir(ctx, 0.8)
        m = self._RE.match(ctx.text)
        if not m:
            ir.errors.append("apache_error_mismatch")
            ir.confidence = 0.3
            return ir
        ir.timestamp = parse_timestamp(m["ts"])[0]
        lvl = m["lvl"].split(":")[-1]
        ir.fields.update({"level": lvl, "module": m["lvl"].split(":")[0] if ":" in m["lvl"] else None, "pid": m["pid"], "client": m["client"]})
        ir.severity = level_to_severity(lvl)
        ir.message = m["msg"]
        ir.vendor, ir.product = "Apache", "httpd"
        return ir


# --------------------------------------------------------------------------- log4j-ish "YYYY-MM-DD HH:MM:SS,mmm - LEVEL [thread] msg"
class Log4jParser(Parser):
    id = "structural.log4j"
    _RE = re.compile(r"^(?P<ts>\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?)\s*(?:-\s*)?(?P<lvl>TRACE|DEBUG|INFO|WARN|WARNING|ERROR|FATAL|SEVERE|FINE)\s*(?:\[(?P<thread>[^\]]*)\])?\s*(?:-\s*)?(?P<logger>[\w.$]+)?\s*[:-]?\s*(?P<msg>.*)$", re.S)
    _RE2 = re.compile(r"^(?P<ts>\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?)\s+(?P<lvl>TRACE|DEBUG|INFO|WARN|WARNING|ERROR|FATAL)\s+(?P<msg>.*)$", re.S)

    def can_parse(self, ctx: ParseContext) -> float:
        t = ctx.text[:80]
        if re.match(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}", t) and re.search(r"\b(TRACE|DEBUG|INFO|WARN|WARNING|ERROR|FATAL|SEVERE)\b", t):
            return 0.8
        return 0.0

    def parse(self, ctx: ParseContext) -> IR:
        ir = self.new_ir(ctx, 0.75)
        m = self._RE.match(ctx.text) or self._RE2.match(ctx.text)
        if not m:
            ir.errors.append("log4j_mismatch")
            ir.confidence = 0.3
            return ir
        d = m.groupdict()
        ir.timestamp = parse_timestamp(d["ts"])[0]
        ir.severity = level_to_severity(d["lvl"])
        ir.fields["level"] = d["lvl"]
        if d.get("thread"):
            ir.fields["thread"] = d["thread"]
        if d.get("logger"):
            ir.fields["logger"] = d["logger"]
        ir.message = d["msg"]
        return ir


STRUCTURAL_PARSERS: list[Parser] = [JSONParser(), CEFParser(), LEEFParser(), Syslog5424Parser(), Syslog3164Parser(), CLFParser(), ApacheErrorParser(), Log4jParser(), KVParser()]
