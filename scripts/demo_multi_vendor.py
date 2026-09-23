#!/usr/bin/env python3
"""Multi-vendor demo: unlike scripts/demo.py's scenario 1 (which deliberately reads a single fixed
line from one dataset for a reproducible walkthrough), this iterates every real downloaded dataset
under logs/samples/ and shows tier/confidence/parser side by side -- proof that classification
genuinely differs by format, not a canned single example.

Usage: python scripts/demo_multi_vendor.py [--base-url http://localhost:8080]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
SAMPLES_DIR = ROOT / "logs" / "samples"


def first_nonblank_line(path: Path) -> str | None:
    for line in path.read_text(errors="replace").splitlines():
        if line.strip():
            return line
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://localhost:8080")
    args = ap.parse_args()

    try:
        with httpx.Client(base_url=args.base_url, timeout=10.0) as c:
            c.get("/health").raise_for_status()
    except Exception as e:  # noqa: BLE001
        print(f"cannot reach {args.base_url}/health ({e}). Run `docker compose up -d` first.", file=sys.stderr)
        return 1

    datasets = sorted(d for d in SAMPLES_DIR.iterdir() if d.is_dir())
    rows: list[tuple[str, int, float, str, str]] = []
    with httpx.Client(base_url=args.base_url, timeout=15.0) as c:
        for d in datasets:
            log_file = d / f"{d.name}.log"
            if not log_file.exists():
                continue
            line = first_nonblank_line(log_file)
            if line is None:
                continue
            source_id = f"multivendor-{d.name}"
            r = c.post("/v1/ingest", json={"source_id": source_id, "lines": [line]})
            r.raise_for_status()
            event_id = r.json()["event_ids"][0]
            ev = c.get(f"/v1/events/{event_id}").json()
            prov = ev["provenance"]
            rows.append((d.name, prov["tier"], prov["confidence"], prov["parser_id"], line[:70]))

    print(f"\n{'vendor':<12} {'tier':<5} {'confidence':<11} {'parser':<28} sample line")
    print("-" * 110)
    for name, tier, conf, parser, sample in rows:
        print(f"{name:<12} {tier:<5} {conf:<11} {parser:<28} {sample}")

    print(f"\n{len(rows)} real, independently-downloaded vendor datasets, {len(set(r[3] for r in rows))} distinct parsers used.")
    print("Nothing here is hardcoded per-vendor logic in this script -- every line went through the same")
    print("POST /v1/ingest -> 7-tier engine -> GET /v1/events round trip as scripts/demo.py's scenario 1.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
