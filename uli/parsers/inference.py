"""Tier-3: field/type inference. Runs on *every* event after tiers 1/2 (and again on tier-4 output).
Extracts observables (IPs, hosts, users, URLs, emails, hashes, ports), a timestamp, a severity and
harvests key=value pairs found anywhere in the message. Total function."""
from __future__ import annotations

import re

from uli.fingerprint import _RE_EMAIL, _RE_HOST, _RE_IP4, _RE_IP6, _RE_MAC, _RE_URL
from uli.models import IR, Observable, ParseContext, TokenType
from uli.parsers.structural import level_to_severity
from uli.timestamps import parse_timestamp

# OCSF observable type_ids (1.x): 1 Hostname, 2 IP, 3 MAC, 4 User Name, 5 Email, 6 URL, 7 File Name, 8 Hash, 10 Port, 20 Process Name
_HASH = re.compile(r"\b(?:[a-fA-F0-9]{32}|[a-fA-F0-9]{40}|[a-fA-F0-9]{64})\b")
_USER_PATTERNS = [
    re.compile(r"\b(?:user|username|usr|uid|account|login)[=: ]+['\"]?(?P<u>[A-Za-z0-9._\-\\@$]{1,64})", re.I),
    re.compile(r"\bfor (?:invalid user |illegal user |user )?(?P<u>[A-Za-z0-9._\-]{1,32}) from \d", re.I),
    re.compile(r"\bsession opened for user (?P<u>[A-Za-z0-9._\-]{1,32})", re.I),
    re.compile(r"\bAccepted \w+ for (?P<u>[A-Za-z0-9._\-]{1,32}) from", re.I),
]
_KV_ANY = re.compile(r"(?<![\w.])(?P<k>[A-Za-z_][\w\-]{0,40})=(?P<v>\"(?:\\\"|[^\"])*\"|'[^']*'|[^\s,;\]\)]+)")
_SEV_ANY = re.compile(r"\b(EMERG|EMERGENCY|ALERT|CRIT|CRITICAL|FATAL|SEVERE|ERROR|ERR|WARN|WARNING|NOTICE|INFO|INFORMATIONAL|DEBUG|TRACE)\b")
_PORT_KEYS = ("port", "sport", "dport", "src_port", "dst_port", "spt", "dpt", "srcport", "dstport")
_IP_KEYS_SRC = ("src", "src_ip", "srcip", "source", "source_ip", "client", "client.ip", "remote_addr", "remote", "id.orig_h", "from", "saddr", "sip", "clientip")
_IP_KEYS_DST = ("dst", "dst_ip", "dstip", "dest", "destination", "dest_ip", "server", "server_ip", "id.resp_h", "to", "daddr", "dip", "target")
_PRIVATE_KEY_SUFFIX = ("password", "passwd", "secret", "token", "apikey", "api_key", "authorization")


def infer(ctx: ParseContext, ir: IR) -> IR:
    text = ctx.text
    try:
        _infer_from_tokens(ctx, ir)
        _infer_kv(text, ir)
        _infer_users(text, ir)
        _infer_hashes(text, ir)
        if ir.timestamp is None:
            ts, gram, orig = parse_timestamp(text)
            if ts is not None:
                ir.timestamp = ts
                ir.fields.setdefault("__ts_grammar", gram)
                ir.fields.setdefault("__ts_original", orig)
        if ir.severity is None:
            m = _SEV_ANY.search(text[:200])
            if m:
                ir.severity = level_to_severity(m.group(1))
        _endpoint_roles(ir)
        _redact(ir)
    except Exception as e:  # noqa: BLE001  — total function
        ir.errors.append(f"inference_error:{type(e).__name__}")
    return ir


def _add_obs(ir: IR, name: str, type_id: int, value: str) -> None:
    if not value:
        return
    for o in ir.observables:
        if o.type_id == type_id and o.value == value:
            return
    if len(ir.observables) < 64:
        ir.observables.append(Observable(name=name, type_id=type_id, value=value))


def _infer_from_tokens(ctx: ParseContext, ir: IR) -> None:
    fp = ctx.fingerprint
    ips: list[str] = []
    for tok, cls in zip(fp.tokens, fp.classes):
        if cls == TokenType.IP4:
            ip = tok.split(":")[0] if tok.count(":") == 1 else tok
            ips.append(ip)
            _add_obs(ir, "ip", 2, ip)
            if ":" in tok and tok.count(":") == 1:
                _add_obs(ir, "port", 10, tok.rsplit(":", 1)[1])
        elif cls == TokenType.IP6:
            if _RE_IP6.fullmatch(tok) and tok.count(":") >= 2 and not _RE_MAC.fullmatch(tok):
                ips.append(tok)
                _add_obs(ir, "ip", 2, tok)
        elif cls == TokenType.MAC:
            _add_obs(ir, "mac", 3, tok)
        elif cls == TokenType.URL:
            _add_obs(ir, "url", 6, tok.rstrip('",;)'))
        elif cls == TokenType.EMAIL:
            _add_obs(ir, "email", 5, tok)
        elif cls == TokenType.HOST:
            if not tok.replace(".", "").isdigit() and not tok.endswith((".log", ".txt", ".py", ".java", ".c", ".so")):
                _add_obs(ir, "hostname", 1, tok)
        elif cls == TokenType.PATH and len(tok) > 3:
            _add_obs(ir, "file", 7, tok)
    if ips:
        ir.fields.setdefault("__ips", ips[:16])


def _infer_kv(text: str, ir: IR) -> None:
    n = 0
    for m in _KV_ANY.finditer(text):
        k, v = m["k"], m["v"]
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        if k not in ir.fields and f"inferred.{k}" not in ir.fields:
            ir.fields[f"inferred.{k}"] = v
        n += 1
        if n >= 64:
            break


def _infer_users(text: str, ir: IR) -> None:
    for pat in _USER_PATTERNS:
        m = pat.search(text)
        if m:
            u = m["u"].strip("'\"")
            if u and u.lower() not in ("none", "null", "-", "unknown"):
                ir.fields.setdefault("inferred.user", u)
                _add_obs(ir, "user", 4, u)
                return


def _infer_hashes(text: str, ir: IR) -> None:
    for m in _HASH.finditer(text):
        _add_obs(ir, "hash", 8, m.group(0))


def _endpoint_roles(ir: IR) -> None:
    """Map obvious keys to src/dst endpoint fields so OCSF Network Activity can be filled without a pack."""
    f = ir.fields
    low = {k.lower().removeprefix("inferred."): k for k in f}

    def pick(keys):
        for k in keys:
            if k in low and f[low[k]] not in (None, ""):
                return str(f[low[k]])
        return None

    src = pick(_IP_KEYS_SRC)
    dst = pick(_IP_KEYS_DST)
    if src and (_RE_IP4.fullmatch(src.split(":")[0]) or _RE_IP6.fullmatch(src)):
        f.setdefault("__src_ip", src.split(":")[0] if src.count(":") == 1 else src)
    if dst and (_RE_IP4.fullmatch(dst.split(":")[0]) or _RE_IP6.fullmatch(dst)):
        f.setdefault("__dst_ip", dst.split(":")[0] if dst.count(":") == 1 else dst)
    if "__src_ip" not in f and "__dst_ip" not in f and f.get("__ips"):
        ips = f["__ips"]
        f["__src_ip"] = ips[0]
        if len(ips) > 1:
            f["__dst_ip"] = ips[1]
    for k in ("spt", "src_port", "srcport", "sport", "id.orig_p", "client.port"):
        if k in low:
            f.setdefault("__src_port", f[low[k]])
            break
    for k in ("dpt", "dst_port", "dstport", "dport", "id.resp_p", "server.port"):
        if k in low:
            f.setdefault("__dst_port", f[low[k]])
            break
    for k in ("proto", "protocol", "ip_proto", "transport"):
        if k in low:
            f.setdefault("__proto", str(f[low[k]]).lower())
            break


def _redact(ir: IR) -> None:
    for k in list(ir.fields):
        kl = k.lower()
        if kl.endswith(_PRIVATE_KEY_SUFFIX) and ir.fields[k] not in (None, ""):
            ir.fields[k] = "***REDACTED***"
