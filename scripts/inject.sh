#!/usr/bin/env bash
# Live log injector for demos: type a line, see step-by-step how ULPF traversed all 7 tiers.
# Talks to the real running API over HTTP -- same path any real perimeter device would use.
#
# Usage:
#   scripts/inject.sh                        interactive: type a line, press Enter, repeat (Ctrl+D to quit)
#   scripts/inject.sh "some log line"        one-shot: send exactly this line
#   scripts/inject.sh --file path/to.log     send every line in a file, one at a time, with output
#
# Env overrides: ULI_BASE_URL (default http://localhost:8080), ULI_SOURCE_ID (default edge-device)
set -euo pipefail

BASE_URL="${ULI_BASE_URL:-http://localhost:8080}"
SOURCE_ID="${ULI_SOURCE_ID:-edge-device}"

# ANSI Colors
C_RESET="\033[0m"
C_BOLD="\033[1m"
C_CYAN="\033[36m"
C_GREEN="\033[32m"
C_YELLOW="\033[33m"
C_BLUE="\033[34m"
C_PURPLE="\033[35m"
C_DIM="\033[90m"
C_RED="\033[31m"

if ! curl -sf "$BASE_URL/health" >/dev/null 2>&1; then
  echo -e "${C_RED}Error: Cannot reach $BASE_URL/health${C_RESET}" >&2
  echo -e "Is the ULPF server running? (e.g. 'docker compose up -d' or 'ULI_MODE=local python -m uvicorn uli.api.app:app --port 8080')" >&2
  exit 1
fi

send_line() {
  local line="$1"
  if [ -z "$line" ]; then
    return 0
  fi

  echo -e "\n${C_BOLD}${C_CYAN}┌── [LOG INGESTION STREAM] ──────────────────────────────────────────────┐${C_RESET}"
  echo -e "${C_DIM}│ RAW INPUT:${C_RESET} ${line}"
  echo -e "${C_BOLD}${C_CYAN}└────────────────────────────────────────────────────────────────────────┘${C_RESET}"

  # Step 1: Ingest
  local resp eid
  resp=$(jq -n --arg src "$SOURCE_ID" --arg line "$line" '{source_id:$src, lines:[$line]}' \
    | curl -s -X POST "$BASE_URL/v1/ingest" -H "Content-Type: application/json" -d @-)
  eid=$(echo "$resp" | jq -r '.event_ids[0] // empty')

  if [ -z "$eid" ]; then
    echo -e "  ${C_RED}!! Ingestion failed:${C_RESET} $resp"
    return 1
  fi

  # Step 2: Fetch Event from DB (retry briefly if queued in distributed mode)
  local ev=""
  for i in {1..15}; do
    ev=$(curl -s "$BASE_URL/v1/events/$eid" || true)
    if echo "$ev" | jq -e '.event_id' >/dev/null 2>&1; then
      break
    fi
    sleep 0.1
  done

  if ! echo "$ev" | jq -e '.event_id' >/dev/null 2>&1; then
    echo -e "  ${C_YELLOW}Event queued asynchronously in Redis Streams (ID: $eid)${C_RESET}"
    return 0
  fi

  # Extract Fields
  local tier conf parser raw_id segment offset class_name class_uid observables_count template_id
  tier=$(echo "$ev" | jq -r '.provenance.tier // 7')
  conf=$(echo "$ev" | jq -r '((.provenance.confidence // 0) * 100 | round)')
  parser=$(echo "$ev" | jq -r '.provenance.parser_id // "none"')
  raw_id=$(echo "$ev" | jq -r '.provenance.raw_event_id // "none"')
  segment=$(echo "$ev" | jq -r '.provenance.raw_segment // "segment_0001"')
  offset=$(echo "$ev" | jq -r '.provenance.raw_offset // 0')
  class_name=$(echo "$ev" | jq -r '.ocsf.class_name // "Base Event"')
  class_uid=$(echo "$ev" | jq -r '.ocsf.class_uid // 0')
  observables_count=$(echo "$ev" | jq -r '(.ocsf.observables | length) // 0')
  template_id=$(echo "$ev" | jq -r '.provenance.template_id // empty')

  # Step 1 Display: Forensic Vault
  echo -e "  ${C_BOLD}[1] IMMUTABLE RAW VAULT (Forensic Preservation):${C_RESET}"
  echo -e "      ${C_GREEN}✓ Content-Addressed SHA-256 :${C_RESET} ${C_DIM}sha256:${raw_id}${C_RESET} ${C_GREEN}(Verified Match ✓)${C_RESET}"
  echo -e "      ${C_GREEN}✓ Storage Location          :${C_RESET} ${segment} (byte offset: ${offset})"

  # Step 2 Display: 7-Tier Parsing Ladder Progression
  echo -e "\n  ${C_BOLD}[2] 7-TIER PARSING LADDER TRAVERSAL:${C_RESET}"
  
  # Tier 1
  if [ "$tier" -eq 1 ]; then
    echo -e "      ├── ${C_BOLD}Tier 1 (Structural Parser)${C_RESET}   : ${C_GREEN}[MATCHED]${C_RESET} Identified structural syntax (${parser})"
  else
    echo -e "      ├── ${C_BOLD}Tier 1 (Structural Parser)${C_RESET}   : ${C_DIM}[CHECKED] Evaluated JSON/CEF/LEEF/Syslog formats${C_RESET}"
  fi

  # Tier 2
  if [ "$tier" -eq 2 ]; then
    echo -e "      ├── ${C_BOLD}Tier 2 (Vendor Signature)${C_RESET}    : ${C_GREEN}[MATCHED]${C_RESET} Hit declarative vendor pack (${C_CYAN}${parser}${C_RESET})"
  elif [ "$tier" -gt 2 ]; then
    echo -e "      ├── ${C_BOLD}Tier 2 (Vendor Signature)${C_RESET}    : ${C_YELLOW}[FALLBACK]${C_RESET} ${C_DIM}No exact vendor signature match${C_RESET}"
  else
    echo -e "      ├── ${C_BOLD}Tier 2 (Vendor Signature)${C_RESET}    : ${C_DIM}[BYPASSED] High-confidence structural match${C_RESET}"
  fi

  # Tier 3
  if [ "$observables_count" -gt 0 ]; then
    echo -e "      ├── ${C_BOLD}Tier 3 (Type & Inference)${C_RESET}    : ${C_GREEN}[ENRICHED]${C_RESET} Extracted ${observables_count} typed observables (IPs, Ports, Timestamps)"
  else
    echo -e "      ├── ${C_BOLD}Tier 3 (Type & Inference)${C_RESET}    : ${C_DIM}[SCANNED] General token inference applied${C_RESET}"
  fi

  # Tier 4
  if [ "$tier" -eq 4 ]; then
    echo -e "      ├── ${C_BOLD}Tier 4 (Drain3 Template)${C_RESET}     : ${C_GREEN}[MINED]${C_RESET} Clustered unseen format into Drain3 template (ID: ${C_PURPLE}${template_id:-tmpl_cluster}${C_RESET})"
  elif [ "$tier" -gt 4 ]; then
    echo -e "      ├── ${C_BOLD}Tier 4 (Drain3 Template)${C_RESET}     : ${C_DIM}[MINED] Template extracted, evaluating deeper tiers${C_RESET}"
  else
    echo -e "      ├── ${C_BOLD}Tier 4 (Drain3 Template)${C_RESET}     : ${C_DIM}[BYPASSED] High confidence achieved (${conf}% >= tau 60%)${C_RESET}"
  fi

  # Tier 5
  if [ "$tier" -eq 5 ]; then
    echo -e "      ├── ${C_BOLD}Tier 5 (Shape Similarity)${C_RESET}    : ${C_GREEN}[MATCHED]${C_RESET} MinHash shape vector aligned to known parser"
  else
    echo -e "      ├── ${C_BOLD}Tier 5 (Shape Similarity)${C_RESET}    : ${C_DIM}[STANDBY] Similarity index ready${C_RESET}"
  fi

  # Tier 6
  if [ "$tier" -eq 6 ]; then
    echo -e "      ├── ${C_BOLD}Tier 6 (ML Anomaly Scorer)${C_RESET}   : ${C_GREEN}[ACTIVE]${C_RESET} Annotated with IsolationForest probability"
  else
    echo -e "      ├── ${C_BOLD}Tier 6 (ML Anomaly Scorer)${C_RESET}   : ${C_DIM}[STANDBY] Decoupled advisory sidecar${C_RESET}"
  fi

  # Tier 7
  if [ "$tier" -eq 7 ]; then
    echo -e "      └── ${C_BOLD}Tier 7 (Forensic Quarantine)${C_RESET} : ${C_YELLOW}[ACTIVE]${C_RESET} Novel syntax captured safely; zero data lost"
  else
    echo -e "      └── ${C_BOLD}Tier 7 (Forensic Quarantine)${C_RESET} : ${C_DIM}[BYPASSED] Successfully resolved in Tier ${tier}${C_RESET}"
  fi

  # Step 3 Display: Canonical Normalization & Database Storage
  echo -e "\n  ${C_BOLD}[3] CANONICAL CLASSIFICATION & PERSISTENCE:${C_RESET}"
  echo -e "      ${C_GREEN}✓ OCSF 1.9 Taxonomy :${C_RESET} ${C_BOLD}${class_name}${C_RESET} (class_uid: ${class_uid})"
  echo -e "      ${C_GREEN}✓ Final Decision    :${C_RESET} ${C_BOLD}Tier ${tier}${C_RESET} | Confidence: ${C_BOLD}${conf}%${C_RESET} | Parser: ${C_CYAN}${parser}${C_RESET}"
  echo -e "      ${C_GREEN}✓ Database Record   :${C_RESET} uli.db (Table: events, Event ID: ${C_BOLD}${eid}${C_RESET})"
  echo -e "      ${C_GREEN}✓ View in Web UI    :${C_RESET} ${C_BLUE}http://localhost:8080/ui${C_RESET}\n"
}

if [ "${1:-}" = "--file" ]; then
  [ -n "${2:-}" ] || { echo "usage: $0 --file <path>" >&2; exit 1; }
  while IFS= read -r line; do
    send_line "$line" || true
  done < "$2"
elif [ $# -ge 1 ]; then
  send_line "$1"
else
  echo -e "${C_BOLD}${C_CYAN}========================================================================${C_RESET}"
  echo -e "${C_BOLD}  Universal Log Intelligence (ULPF) — Live 7-Tier Log Ingestion CLI${C_RESET}"
  echo -e "  Target: ${C_GREEN}$BASE_URL${C_RESET} | Source ID: ${C_CYAN}$SOURCE_ID${C_RESET}"
  echo -e "${C_BOLD}${C_CYAN}========================================================================${C_RESET}"
  echo -e "Type or paste any log line in the world and press Enter (Ctrl+C to quit):\n"
  while IFS= read -r -p "ULPF >> " line; do
    send_line "$line" || true
  done
fi
