#!/usr/bin/env python3
"""Reproducible acquisition of real-world log datasets (docs/research.md §8).

Downloads → verifies checksum → extracts → writes a representative sample (first N lines) into
logs/samples/<name>/ with a LICENSE-NOTE. Full datasets stay in logs/raw-datasets/ (git-ignored).

Usage:  python scripts/fetch_logs.py [--only linux,ssh] [--sample-lines 2000] [--offline]
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import shutil
import sys
import tarfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATASETS_DIR = ROOT / "logs" / "raw-datasets"
SAMPLES_DIR = ROOT / "logs" / "samples"

LOGHUB_NOTE = (
    "Source: Loghub (logpai) — https://github.com/logpai/loghub, Zenodo record 3227177.\n"
    "License: free for research/academic use with citation: Jieming Zhu et al., 'Loghub: A Large Collection of "
    "System Log Datasets for AI-driven Log Analytics', ISSRE 2023. This notice must accompany copies.\n"
)
SECREPO_NOTE = "Source: SecRepo — https://www.secrepo.com (Mike Sconzo). See site for per-file terms; sample redistributed for research/demo with attribution.\n"

# name: (url, checksum_type, checksum, kind, member_glob, vendor, format, note)
MANIFEST: dict[str, dict] = {
    "linux": dict(url="https://zenodo.org/records/3227177/files/Linux.tar.gz?download=1", md5="6d1802d7778126f21c001c6aa7b6b106", kind="tar", member="Linux.log", vendor="Linux (syslog)", fmt="BSD syslog", note=LOGHUB_NOTE),
    "apache": dict(url="https://zenodo.org/records/3227177/files/Apache.tar.gz?download=1", md5="de9a42d12f9b60612631c67a5a9f8628", kind="tar", member="Apache.log", vendor="Apache httpd", fmt="Apache error log", note=LOGHUB_NOTE),
    "ssh": dict(url="https://zenodo.org/records/3227177/files/SSH.tar.gz?download=1", md5="a44e40b4348697dabf2bba56885e0a38", kind="tar", member="SSH.log", vendor="OpenSSH", fmt="BSD syslog (sshd)", note=LOGHUB_NOTE),
    "zookeeper": dict(url="https://zenodo.org/records/3227177/files/Zookeeper.tar.gz?download=1", md5="11458b4f9fa0911c238f5c1189d4f2d4", kind="tar", member="Zookeeper.log", vendor="Apache ZooKeeper", fmt="log4j", note=LOGHUB_NOTE),
    "mac": dict(url="https://zenodo.org/records/3227177/files/Mac.tar.gz?download=1", md5="e5d0558b6ec661c739e77c7ff1b71498", kind="tar", member="Mac.log", vendor="macOS", fmt="BSD syslog", note=LOGHUB_NOTE),
    "healthapp": dict(url="https://zenodo.org/records/3227177/files/HealthApp.tar.gz?download=1", md5="cec2cca71da9c4f8e33eaa9b215f1b90", kind="tar", member="HealthApp.log", vendor="HealthApp (Android)", fmt="pipe-delimited app log", note=LOGHUB_NOTE),
    "proxifier": dict(url="https://zenodo.org/records/3227177/files/Proxifier.tar.gz?download=1", md5="2612fce12cc3d16599ddb3db8e9c477a", kind="tar", member="Proxifier.log", vendor="Proxifier", fmt="proxy client log", note=LOGHUB_NOTE),
    "hpc": dict(url="https://zenodo.org/records/3227177/files/HPC.tar.gz?download=1", md5="4a7f0483f7b4fdd8b278c266039db464", kind="tar", member="HPC.log", vendor="HPC cluster", fmt="space-delimited", note=LOGHUB_NOTE),
    "hadoop": dict(url="https://zenodo.org/records/3227177/files/Hadoop.tar.gz?download=1", md5="6dfc442b9e86adb519db9437d4e7d3d4", kind="tar", member="*.log", vendor="Apache Hadoop", fmt="log4j", note=LOGHUB_NOTE),
    "squid": dict(url="https://www.secrepo.com/squid/access.log.gz", md5=None, kind="gz", member=None, vendor="Squid proxy", fmt="Squid native access log", note=SECREPO_NOTE),
    "zeek_conn": dict(url="https://www.secrepo.com/maccdc2012/conn.log.gz", md5=None, kind="gz", member=None, vendor="Zeek/Bro", fmt="Zeek TSV conn.log", note=SECREPO_NOTE),
    "auth": dict(url="https://www.secrepo.com/auth.log/auth.log.gz", md5=None, kind="gz", member=None, vendor="Linux auth.log", fmt="BSD syslog", note=SECREPO_NOTE),
}


def md5sum(p: Path) -> str:
    h = hashlib.md5()
    with open(p, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "uli-fetch/0.1"})
    with urllib.request.urlopen(req, timeout=120) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f)


def extract_lines(archive: Path, kind: str, member: str | None, n: int) -> tuple[list[str], int]:
    lines: list[str] = []
    total = 0
    if kind == "tar":
        with tarfile.open(archive, "r:gz") as t:
            for m in t.getmembers():
                if not m.isfile():
                    continue
                name = Path(m.name).name
                if member and not (name == member or (member.startswith("*") and name.endswith(member[1:]))):
                    continue
                fh = t.extractfile(m)
                if fh is None:
                    continue
                for raw in io.TextIOWrapper(fh, encoding="utf-8", errors="replace"):
                    total += 1
                    if len(lines) < n:
                        lines.append(raw.rstrip("\n"))
                if lines and not member.startswith("*"):
                    break
    else:
        with gzip.open(archive, "rt", encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                total += 1
                if len(lines) < n:
                    lines.append(raw.rstrip("\n"))
    return lines, total


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="comma list of dataset names")
    ap.add_argument("--sample-lines", type=int, default=2000)
    ap.add_argument("--offline", action="store_true", help="only (re)build samples from already downloaded archives")
    a = ap.parse_args()
    names = [n.strip() for n in a.only.split(",") if n.strip()] or list(MANIFEST)
    ok = True
    for name in names:
        spec = MANIFEST[name]
        ext = ".tar.gz" if spec["kind"] == "tar" else ".gz"
        archive = DATASETS_DIR / f"{name}{ext}"
        if not archive.exists():
            if a.offline:
                print(f"[skip] {name}: archive missing and --offline", file=sys.stderr)
                continue
            print(f"[get ] {name} ← {spec['url']}")
            try:
                download(spec["url"], archive)
            except Exception as e:  # noqa: BLE001
                print(f"[fail] {name}: {e}", file=sys.stderr)
                ok = False
                continue
        if spec["md5"]:
            got = md5sum(archive)
            if got != spec["md5"]:
                print(f"[fail] {name}: md5 {got} != {spec['md5']}", file=sys.stderr)
                ok = False
                continue
        lines, total = extract_lines(archive, spec["kind"], spec["member"], a.sample_lines)
        out = SAMPLES_DIR / name
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{name}.log").write_text("\n".join(lines) + "\n", encoding="utf-8")
        (out / "LICENSE-NOTE.txt").write_text(spec["note"] + f"\nVendor/format: {spec['vendor']} / {spec['fmt']}\nSample: first {len(lines)} of {total} lines.\nArchive md5: {md5sum(archive)}\n")
        print(f"[ ok ] {name}: {len(lines)}/{total} lines → {out}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
