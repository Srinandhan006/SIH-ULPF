# Data Model

Schema files live in `schemas/normalized/` (JSON Schema, versioned). This document is the human explanation.

## 1. Versioning

| Thing | Version field | Where |
|---|---|---|
| Envelope | `schema_version` = `"uli.v1"` | every normalized event |
| OCSF body | `metadata.version` = `"1.9.0"` | every normalized event |
| Parser | `parser_id` + `parser_version` (semver) | every normalized event; parser registry |
| API | URL prefix `/v1/` | API |
| Drain3 state | `template_miner_version` | source record |

Breaking changes to the envelope bump `uli.vN` and require a migration entry in `storage/migrations/`.

## 2. NormalizedEvent (`schemas/normalized/event.uli.v1.json`)

```json
{
  "event_id": "01J9…ULID",
  "schema_version": "uli.v1",
  "tenant_id": "default",
  "project_id": "default",
  "source_id": "syslog:10.0.0.5",

  "provenance": {
    "raw_event_id": "sha256:…",
    "raw_segment": "default/syslog:10.0.0.5/2026-09-05/000001.ndjson",
    "raw_offset": 48213,
    "raw_length": 212,
    "received_at": "2026-09-05T10:00:00.123Z",
    "processed_at": "2026-09-05T10:00:00.140Z",
    "transport": "syslog-udp",
    "peer": "10.0.0.5:514",
    "parser_id": "vendor.pfsense.filterlog",
    "parser_version": "1.2.0",
    "normalizer_version": "1.0.0",
    "template_id": "drain:src:17",
    "tier": 2,
    "confidence": 0.93,
    "duplicate_of": null,
    "processing_errors": []
  },

  "ocsf": {
    "class_uid": 4001,
    "class_name": "Network Activity",
    "category_uid": 4,
    "activity_id": 1,
    "type_uid": 400101,
    "severity_id": 1,
    "time": 1757066400123,
    "metadata": {
      "version": "1.9.0",
      "product": {"vendor_name": "Netgate", "name": "pfSense", "version": "2.7"},
      "log_name": "filterlog",
      "original_time": "Sep  5 10:00:00",
      "uid": "01J9…ULID"
    },
    "src_endpoint": {"ip": "10.0.0.7", "port": 51234},
    "dst_endpoint": {"ip": "93.184.216.34", "port": 443},
    "connection_info": {"protocol_name": "tcp", "direction_id": 2},
    "action_id": 2,
    "disposition_id": 2,
    "observables": [
      {"name": "src_endpoint.ip", "type_id": 2, "value": "10.0.0.7"}
    ],
    "unmapped": {"rule_number": "1000000103", "tracker": "1000000103"},
    "raw_data": null
  },

  "message": "…human message / body…",
  "anomaly": {"score": null, "model": null, "suppressed_reason": null}
}
```

Notes:
- `ocsf.raw_data` is **null by default** to avoid doubling storage; the raw is one hop away via `provenance.raw_event_id`. Set `ULI_EMBED_RAW=true` to inline it (useful for SIEM export).
- `unmapped` holds *every* extracted field that had no OCSF mapping — nothing extracted is discarded.
- `observables` are always populated by tier-3 inference even when the class is unknown, so unknown-vendor events are still searchable by IP/host/user.
- Unknown class → `class_uid: 0` (`Base Event`) with `category_uid: 0`; the event remains valid OCSF.

## 3. Raw records

```
RawRecord      { raw_event_id (sha256), tenant_id, source_id, transport, peer,
                 received_at, payload (bytes), length }
raw_index (SQL){ raw_event_id PK, tenant_id, source_id, segment, offset, length, received_at }
```

Immutability: segment files are opened append-only, never rewritten; gzip on rotation produces a new file, the index is updated transactionally, the plain file is removed only after the gz is fsync'd and hash-verified.

## 4. Sources

```
Source { source_id PK, tenant_id, display_name, transport, first_seen, last_seen,
         event_count, active_parser_id, parser_health ("healthy"|"degraded"|"unknown"),
         drain_state_key, reference_histogram (json), reference_captured_at }
```

## 5. Parsers (registry)

```
Parser { parser_id PK, version, kind ("structural"|"declarative"|"python"|"suggested"),
         vendor, product, signatures (json), ocsf_class_default, bundle_sha256, signature_ok (bool),
         compat: {envelope: "uli.v1", ocsf: ">=1.6 <2"}, status ("active"|"candidate"|"retired"),
         created_at, promoted_at, promoted_by, previous_version }
```

## 6. Fingerprints

```
Fingerprint { shape_hash PK, family_hash, shape (string), token_classes (json), example_raw_event_id,
              first_seen, last_seen, count, routed_parser_id (nullable) }
```

`shape` example: `TS HOST WORD[NUM]: KV(WORD=IP) KV(WORD=NUM) WORD WORD` — a line reduced to token classes with literal delimiters preserved.

## 7. Unknown registry

```
UnknownCluster { cluster_id PK, tenant_id, source_id, family_hash, shape_hashes (json),
                 template_ids (json), inferred_fields (json: name → {type, fill_rate, example}),
                 candidate_parsers (json: [{parser_id, similarity}]), confidence,
                 status ("open"|"suggested"|"promoted"|"dismissed"), event_count, first_seen, last_seen }
ParserSuggestion { suggestion_id PK, cluster_id | source_id, origin ("unknown"|"drift"),
                   base_parser_id, yaml (text), score, created_at, status }
```

## 8. Drift events

```
DriftEvent  (resource)  { drift_id PK, tenant_id, kind ("cpu"|"memory"|"disk"|"image"|"env"|"config"|
                          "parser_version"|"schema_version"|"k8s"), key, desired, observed, severity,
                          detected_at, snapshot_raw_event_id (raw store hash of the observed snapshot),
                          desired_state_sha256 }
ParserDriftEvent        { pdrift_id PK, tenant_id, source_id, parser_id, parser_version, detected_at,
                          js_divergence, confidence_before, confidence_after, reference_histogram,
                          current_histogram, window_size, explained_by (json: [drift_id…]),
                          suggestion_id (nullable) }
```

Both are also written as `NormalizedEvent`s with `ocsf.class_uid = 2004` (Detection Finding) / `5001` (Device Config State Change) as appropriate, so one query surface covers logs *and* drift.

## 9. Audit

```
Audit { id, tenant_id, actor, action ("parser.promote"|"parser.retire"|"bundle.load"|…), target, at, details }
```

## 10. Tenant isolation rules

- Every query is scoped by `tenant_id` derived from the authenticated key — never from the request body.
- Raw paths and Drain3 state keys are prefixed with the tenant.
- Parser packs are global (they are code-like artifacts) but *activation* is per tenant (`TenantParserActivation {tenant_id, parser_id, enabled}`).
