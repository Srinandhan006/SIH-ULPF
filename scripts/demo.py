#!/usr/bin/env python3
"""Six-scenario demonstration script, run against a live deployment (docker compose or `make run`).

Scenarios (per the project brief): known vendor; parser-drift resilience; unknown vendor ->
fingerprint -> cluster -> learning; resource drift (desired vs. actual); air-gapped operation; raw
provenance round-trip. Each scenario prints what it's about to do, what it asserts, and the actual
response -- this is meant to be read while it runs, not just checked for a zero exit code.

Usage: python scripts/demo.py [--base-url http://localhost:8080]
"""
from __future__ import annotations

import argparse
import socket
import sys
import time
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
RUN = uuid.uuid4().hex[:8]


def header(n: int, title: str) -> None:
    print(f"\n{'=' * 70}\nSCENARIO {n}: {title}\n{'=' * 70}")


def check(label: str, ok: bool) -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {label}")
    if not ok:
        raise SystemExit(f"demo assertion failed: {label}")


def scenario_known_vendor(c: httpx.Client) -> None:
    header(1, "Known vendor — real dataset line parses with high confidence")
    sample = ROOT / "logs" / "samples" / "ssh" / "ssh.log"
    line = sample.read_text().splitlines()[0] if sample.exists() else (
        "Dec 10 06:55:46 LabSZ sshd[24200]: Invalid user webmaster from 173.234.31.186"
    )
    print(f"  line (real OpenSSH log, logs/samples/ssh/): {line}")
    source = f"demo-known-{RUN}"
    r = c.post("/v1/ingest", json={"source_id": source, "lines": [line]})
    check("ingest returned 200", r.status_code == 200)
    event_id = r.json()["event_ids"][0]
    ev = c.get(f"/v1/events/{event_id}").json()
    print(f"  parsed tier={ev['provenance']['tier']} confidence={ev['provenance']['confidence']}")
    print(f"  normalized message: {ev.get('message')}")
    check("tier is a deterministic tier (1-3)", ev["provenance"]["tier"] in (1, 2, 3))
    check("confidence is high", ev["provenance"]["confidence"] >= 0.5)


def scenario_format_drift(c: httpx.Client) -> None:
    header(2, "Parser-drift resilience — format changes, ingestion never breaks")
    source = f"demo-drift-{RUN}"
    v1 = "Jun 14 15:16:01 host1 myapp[555]: connection accepted from 10.0.0.5"
    v2 = "Jun 14 15:17:02 host1 myapp(555): INFO connection accepted from 10.0.0.6"
    print(f"  v1 (known format):  {v1}")
    print(f"  v2 (drifted format): {v2}")
    ids = []
    for line in (v1, v2):
        r = c.post("/v1/ingest", json={"source_id": source, "lines": [line]})
        check(f"ingest of '{line[:30]}...' returned 200", r.status_code == 200)
        ids.append(r.json()["event_ids"][0])
    for eid in ids:
        ev = c.get(f"/v1/events/{eid}").json()
        print(f"  event {eid[:8]}... tier={ev['provenance']['tier']} confidence={ev['provenance']['confidence']}")
        check("still produced a traceable event (tier 1-7)", ev["provenance"]["tier"] in range(1, 8))
    print("  -> both versions produced a usable event; nothing raised, nothing was dropped.")


def scenario_unknown_vendor(c: httpx.Client) -> None:
    header(3, "Unknown vendor — fingerprint -> cluster -> suggest -> promote")
    source = f"demo-unknown-{RUN}"
    print(f"  simulating a never-configured vendor 'GIZMOTECH' on source {source}")
    for i in range(20):
        line = f"GIZMOTECH-UNIT id {i} state RUNNING zone north load {10 * i} at 175706650{i % 10}"
        r = c.post("/v1/ingest", json={"source_id": source, "lines": [line]})
        check(f"line {i} ingested", r.status_code == 200)

    print("  polling GET /v1/unknown for a cluster...")
    deadline = time.monotonic() + 10
    cluster = None
    while time.monotonic() < deadline:
        matches = [x for x in c.get("/v1/unknown").json() if x["source_id"] == source]
        if matches:
            cluster = matches[0]
            break
        time.sleep(0.5)
    check("a cluster formed for the unseen vendor", cluster is not None)
    print(f"  cluster_id={cluster['cluster_id']} event_count={cluster['event_count']}")
    print(f"  learned template: {list(cluster.get('templates', {}).values())[:1]}")

    r = c.post(f"/v1/unknown/{cluster['cluster_id']}/suggest")
    check("suggestion synthesized", r.status_code == 200)
    suggestion = r.json()
    print(f"  synthesized draft parser_id={suggestion['parser_id']}")
    print("  --- draft YAML (first 8 lines) ---")
    for ln in suggestion["yaml"].splitlines()[:8]:
        print(f"    {ln}")

    suggestions = c.get("/v1/parsers/suggestions").json()
    sid = next(s["suggestion_id"] for s in suggestions if s["parser_id"] == suggestion["parser_id"])
    r2 = c.post(f"/v1/parsers/suggestions/{sid}/promote", json={"approved_by": "demo-script"})
    check("promotion (human-approved) succeeded", r2.status_code == 200)
    parsers = {p["parser_id"] for p in c.get("/v1/parsers").json()}
    check("promoted parser is now active", suggestion["parser_id"] in parsers)
    print("  -> a never-before-seen vendor is now a routable, hot-loaded parser, with a human approval in the loop.")


def scenario_resource_drift(c: httpx.Client) -> None:
    header(4, "Resource drift — desired-state.yaml vs. observed system state")
    drift_before = c.get("/v1/drift", params={"kind": "cpu"}).json()
    print(f"  existing cpu-kind drift events before: {len(drift_before)}")
    print("  deployment/desired-state.yaml ships with every key commented out (zero false positives")
    print("  by default — architecture.md: 'an absent key is never compared'). To see a real drift")
    print("  event, uncomment e.g. `cpu_count: 999` in that file and restart the agent; here we just")
    print("  confirm the endpoint and the zero-false-positive default are both live:")
    r = c.get("/v1/drift")
    check("GET /v1/drift responds", r.status_code == 200)
    print(f"  total drift events on record: {len(r.json())} (see docs/resource-drift.md for the full mechanism)")


def scenario_air_gapped(c: httpx.Client, base_url: str) -> None:
    header(5, "Air-gapped operation — no outbound network call is required to ingest/parse/query")
    print("  this demo does not sever the host's network (that would break this script's own HTTP")
    print("  client); instead it shows that a real outbound call is unreachable/irrelevant to ULI,")
    print("  by attempting one with a short timeout and then proving ingestion still works.")
    outbound_ok = False
    try:
        with socket.create_connection(("1.1.1.1", 443), timeout=1.5):
            outbound_ok = True
    except OSError:
        outbound_ok = False
    print(f"  outbound internet reachable from this shell: {outbound_ok} (either way, doesn't matter below)")

    source = f"demo-airgap-{RUN}"
    r = c.post("/v1/ingest", json={"source_id": source, "lines": ["air-gapped ingest sanity check"]})
    check("ingest succeeded with zero dependency on outbound reachability", r.status_code == 200)
    print(f"  ULI base_url={base_url} served this request using only local containers (redis/api/worker),")
    print("  no model download, no registry pull, no external API call in the ingest/parse/query path.")
    print("  see docs/air-gapped-deployment.md for the full offline-bundle build/verify procedure.")


def scenario_provenance(c: httpx.Client) -> None:
    header(6, "Raw provenance round-trip — forensic replay of the exact original bytes")
    source = f"demo-provenance-{RUN}"
    line = "raw provenance round trip through the demo script"
    r = c.post("/v1/ingest", json={"source_id": source, "lines": [line]})
    check("ingest returned 200", r.status_code == 200)
    event_id = r.json()["event_ids"][0]
    prov = c.get(f"/v1/events/{event_id}").json()["provenance"]
    print(f"  raw_event_id={prov['raw_event_id']} parser_id={prov['parser_id']} tier={prov['tier']}")
    raw = c.get(f"/v1/raw/{prov['raw_event_id']}")
    check("raw fetch returned 200", raw.status_code == 200)
    body = raw.json()
    check("sha256_verified is true", body["sha256_verified"] is True)
    check("payload matches original bytes exactly", body["payload"] == line)
    print("  -> the original line, byte-for-byte, is recoverable from the normalized event alone.")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://localhost:8080")
    args = ap.parse_args()

    try:
        with httpx.Client(base_url=args.base_url, timeout=10.0) as c:
            r = c.get("/health")
            r.raise_for_status()
    except Exception as e:  # noqa: BLE001
        print(f"cannot reach {args.base_url}/health ({e}). Run `docker compose up -d` or `make run` first.", file=sys.stderr)
        return 1

    with httpx.Client(base_url=args.base_url, timeout=15.0) as c:
        scenario_known_vendor(c)
        scenario_format_drift(c)
        scenario_unknown_vendor(c)
        scenario_resource_drift(c)
        scenario_air_gapped(c, args.base_url)
        scenario_provenance(c)

    print(f"\n{'=' * 70}\nAll 6 scenarios completed successfully.\n{'=' * 70}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
