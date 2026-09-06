"""Structural fingerprinting: line → tokens → token classes → shape / shape_hash / family_hash.

Design goals: O(n) in line length, regex only via anchored fullmatch on individual tokens,
identical output across workers (pure function), stable under whitespace changes.
"""
from __future__ import annotations

import hashlib
import re

from uli.models import Fingerprint, TokenType

_MAX_TOKENS = 512

# Structural delimiters are kept as their own tokens so shape preserves layout.
_SPLIT = re.compile(r"(\s+|[=:,|\[\]\(\)\"<>{}])")

_RE_NUM = re.compile(r"[+-]?\d+(?:\.\d+)?")
_RE_HEX = re.compile(r"(?:0x)?[0-9a-fA-F]{6,}")
_RE_IP4 = re.compile(r"(?:\d{1,3}\.){3}\d{1,3}(?::\d{1,5})?")
_RE_IP6 = re.compile(r"(?:[0-9a-fA-F]{0,4}:){2,7}[0-9a-fA-F]{0,4}(?:%\w+)?")
_RE_MAC = re.compile(r"(?:[0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2}")
_RE_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_RE_URL = re.compile(r"[a-zA-Z][a-zA-Z0-9+.-]*://\S+")
_RE_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_RE_PATH = re.compile(r"/(?:[\w.\-]+/)*[\w.\-]*|[A-Za-z]:\\[^\s]*")
_RE_HOST = re.compile(r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,62}\.)+[a-zA-Z]{2,}")
_RE_TS = re.compile(
    r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:[.,]\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?"  # ISO-ish
    r"|\d{2}:\d{2}:\d{2}(?:[.,]\d+)?"  # time only
    r"|\d{8}-\d{2}:\d{2}:\d{2}(?::\d+)?"  # HealthApp 20171223-22:15:29:606
    r"|\d{2}/[A-Za-z]{3}/\d{4}:\d{2}:\d{2}:\d{2}"  # CLF
    r"|\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+"  # Android
    r"|\d{10}(?:\.\d+)?|\d{13}"  # epoch s / ms
)
_MONTHS = {"jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"}
_SEV_WORDS = {"emerg", "alert", "crit", "critical", "error", "err", "warn", "warning", "notice", "info", "informational", "debug", "trace", "fatal", "severe", "fine"}


def classify(tok: str) -> TokenType:
    if not tok:
        return TokenType.PUNCT
    c = tok[0]
    if len(tok) == 1 and not c.isalnum():
        return TokenType.PUNCT
    if c.isdigit() or c in "+-":
        if _RE_IP4.fullmatch(tok):
            return TokenType.IP4
        if _RE_TS.fullmatch(tok):
            return TokenType.TS
        if _RE_NUM.fullmatch(tok):
            return TokenType.NUM
        if _RE_MAC.fullmatch(tok):
            return TokenType.MAC
        if _RE_UUID.fullmatch(tok):
            return TokenType.UUID
        if _RE_HEX.fullmatch(tok):
            return TokenType.HEX
        if ":" in tok and _RE_IP6.fullmatch(tok):
            return TokenType.IP6
    if "://" in tok and _RE_URL.fullmatch(tok):
        return TokenType.URL
    if "@" in tok and _RE_EMAIL.fullmatch(tok):
        return TokenType.EMAIL
    if c == "/" or (len(tok) > 2 and tok[1] == ":" and tok[2] == "\\"):
        if _RE_PATH.fullmatch(tok):
            return TokenType.PATH
    if "-" in tok and len(tok) == 36 and _RE_UUID.fullmatch(tok):
        return TokenType.UUID
    if ":" in tok and _RE_MAC.fullmatch(tok):
        return TokenType.MAC
    if ":" in tok and tok.count(":") >= 2 and _RE_IP6.fullmatch(tok):
        return TokenType.IP6
    if _RE_HEX.fullmatch(tok) and any(ch.isdigit() for ch in tok):
        return TokenType.HEX
    if "." in tok and _RE_HOST.fullmatch(tok):
        return TokenType.HOST
    return TokenType.WORD


_RE_TS_ATOM = re.compile(
    r"\d{2}:\d{2}:\d{2}(?:[.,]\d+)?"  # HH:MM:SS(.fff) — the common case the delimiter splitter would shred
    r"|\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?"  # full ISO
)
_TS_SENTINEL = "\x00TS\x00"


def tokenize(text: str) -> list[str]:
    """Delimiter-split, but first protect timestamp-shaped substrings (HH:MM:SS, ISO 8601) from
    being shredded by the ':' delimiter — otherwise 'classify()' never sees a whole timestamp token
    and both fingerprint shapes and Drain3 masking degrade for the overwhelming majority of logs,
    which put a clock time right after the date."""
    protected: list[str] = []

    def _stash(m: re.Match[str]) -> str:
        protected.append(m.group(0))
        return _TS_SENTINEL

    guarded = _RE_TS_ATOM.sub(_stash, text)
    out: list[str] = []
    pi = 0
    for part in _SPLIT.split(guarded):
        if not part or part.isspace():
            continue
        if _TS_SENTINEL in part:
            part = part.replace(_TS_SENTINEL, protected[pi])
            pi += 1
        out.append(part)
        if len(out) >= _MAX_TOKENS:
            break
    return out


def fingerprint(text: str) -> Fingerprint:
    toks = tokenize(text)
    classes: list[TokenType] = []
    shape_parts: list[str] = []
    i = 0
    n = len(toks)
    while i < n:
        t = toks[i]
        cls = classify(t)
        # key=value pattern: WORD '=' X  → KV
        if cls == TokenType.WORD and i + 2 < n and toks[i + 1] == "=":
            classes.extend([TokenType.KV, TokenType.PUNCT])
            shape_parts.append("KV=")
            i += 2
            continue
        # BSD month name followed by day + time → TS
        if cls == TokenType.WORD and t.lower() in _MONTHS and i + 2 < n and toks[i + 1].isdigit() and classify(toks[i + 2]) == TokenType.TS:
            classes.extend([TokenType.TS, TokenType.TS, TokenType.TS])
            shape_parts.append("TS")
            i += 3
            continue
        classes.append(cls)
        if cls == TokenType.PUNCT:
            shape_parts.append(t)
        elif cls == TokenType.WORD and t.lower() in _SEV_WORDS:
            shape_parts.append("SEV")
        else:
            shape_parts.append(cls.name)
        i += 1
    shape = " ".join(shape_parts)
    shape_hash = hashlib.sha1(shape.encode()).hexdigest()[:16]
    family = _family(shape_parts)
    family_hash = hashlib.sha1(family.encode()).hexdigest()[:16]
    return Fingerprint(shape=shape, shape_hash=shape_hash, family_hash=family_hash, tokens=toks, classes=classes, token_count=len(toks))


def _family(parts: list[str]) -> str:
    """Coarse key: collapse WORD/NUM/HEX runs, keep structural punctuation and typed tokens."""
    out: list[str] = []
    prev = None
    for p in parts:
        q = "W" if p in ("WORD", "NUM", "HEX", "SEV") else p
        if q == prev and q == "W":
            continue
        out.append(q)
        prev = q
    return " ".join(out[:64])


def shape_ngrams(shape: str, n: int = 3) -> set[str]:
    parts = shape.split()
    if len(parts) < n:
        return {" ".join(parts)} if parts else set()
    return {" ".join(parts[i : i + n]) for i in range(len(parts) - n + 1)}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / (len(a) + len(b) - inter)
