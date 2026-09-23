#!/usr/bin/env bash
# Verify an air-gapped bundle's checksum and (if present) Ed25519 signature before running LOAD.sh
# on it. This is the target-side half of docs/problem-statement.md #7 ("signed offline bundle") —
# build_bundle.sh produces the signature, this checks it.
#
# Usage: bash deployment/airgap/verify_bundle.sh <archive.tar.gz> [public-key.pem]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ARCHIVE="$1"
PUBKEY="${2:-}"
DIR="$(cd "$(dirname "$ARCHIVE")" && pwd)"
NAME="$(basename "$ARCHIVE")"

echo "[1/2] checksum..."
if [ -f "$DIR/SHA256SUMS.txt" ]; then
  ( cd "$DIR" && sha256sum --ignore-missing -c SHA256SUMS.txt )
else
  echo "no SHA256SUMS.txt next to the archive — cannot verify checksum" >&2
  exit 1
fi

echo "[2/2] signature..."
if [ -f "$ARCHIVE.sig" ]; then
  if [ -z "$PUBKEY" ]; then
    echo "a .sig file exists ($ARCHIVE.sig) but no public key was given — cannot verify. Refusing to treat this as a signed bundle." >&2
    exit 1
  fi
  python3 "$ROOT/scripts/sign_bundle.py" verify "$ARCHIVE" --sig "$ARCHIVE.sig" --pubkey "$PUBKEY"
else
  echo "WARNING: no $ARCHIVE.sig found — this bundle is checksum-verified only, not signature-verified."
fi

echo "$NAME: checksum OK$( [ -f "$ARCHIVE.sig" ] && [ -n "$PUBKEY" ] && echo ', signature OK' )"
