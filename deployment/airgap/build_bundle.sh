#!/usr/bin/env bash
# Build a self-contained, air-gapped deployment bundle: built Docker images (saved as tarballs,
# no registry needed at the destination), the compose file, vendor parser packs, OCSF schemas,
# sample log datasets (with their license notes), and docs. See docs/air-gapped-deployment.md.
#
# Usage: bash deployment/airgap/build_bundle.sh [output_dir]
# Output: <output_dir>/uli-airgap-<TAG>.tar.gz + SHA256SUMS.txt alongside it.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

TAG="$(date -u +%Y%m%dT%H%M%SZ)"
OUT_DIR="${1:-$ROOT/dist}"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

mkdir -p "$OUT_DIR" "$STAGE/images" "$STAGE/parsers" "$STAGE/schemas" "$STAGE/samples" "$STAGE/docs" "$STAGE/deployment"

echo "[1/5] building images..."
docker compose build api worker collector
docker compose --profile ml build ml

echo "[2/5] saving images to tarballs (no registry needed offline)..."
for img in uli-api uli-worker uli-ml uli-collector; do
  docker save "$img:latest" -o "$STAGE/images/${img}.tar"
done

echo "[3/5] copying parser packs, schemas, sample datasets, docs, compose files..."
cp -r parsers/vendors "$STAGE/parsers/vendors"
cp -r schemas/. "$STAGE/schemas/"
if [ -d logs/samples ]; then cp -r logs/samples "$STAGE/samples"; fi
cp -r docs/. "$STAGE/docs/"
cp docker-compose.yml "$STAGE/deployment/docker-compose.yml"
cp deployment/desired-state.yaml "$STAGE/deployment/desired-state.yaml"
cp README.md "$STAGE/README.md"

cat > "$STAGE/LOAD.sh" <<'EOF'
#!/usr/bin/env bash
# Run on the air-gapped target after extracting the bundle: loads images into the local Docker
# daemon and brings up the stack with no network access required.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
for f in images/*.tar; do docker load -i "$f"; done
docker compose -f deployment/docker-compose.yml up -d
echo "stack up: http://localhost:8080/health"
EOF
chmod +x "$STAGE/LOAD.sh"

echo "[4/5] writing manifest..."
{
  echo "ULI air-gap bundle"
  echo "built_at_utc: $TAG"
  echo "images:"
  for img in uli-api uli-worker uli-ml uli-collector; do
    id="$(docker images --no-trunc --format '{{.ID}}' "$img:latest" | head -1)"
    echo "  - $img:latest ($id)"
  done
} > "$STAGE/MANIFEST.txt"

echo "[5/6] archiving..."
ARCHIVE="$OUT_DIR/uli-airgap-$TAG.tar.gz"
tar -C "$STAGE" -czf "$ARCHIVE" .
( cd "$OUT_DIR" && sha256sum "$(basename "$ARCHIVE")" >> SHA256SUMS.txt )

echo "[6/6] signing..."
if [ -n "${ULI_AIRGAP_SIGNING_KEY:-}" ] && [ -f "$ULI_AIRGAP_SIGNING_KEY" ]; then
  python3 "$ROOT/scripts/sign_bundle.py" sign "$ARCHIVE" --key "$ULI_AIRGAP_SIGNING_KEY" --out "$ARCHIVE.sig"
  echo "signed (Ed25519): $ARCHIVE.sig"
else
  echo "WARNING: ULI_AIRGAP_SIGNING_KEY not set — bundle is checksummed (SHA256SUMS.txt) but NOT"
  echo "  cryptographically signed. This does not satisfy problem-statement.md #7 ('signed offline"
  echo "  bundle') on its own. To sign:"
  echo "    python3 scripts/sign_bundle.py keygen --out-dir deployment/keys"
  echo "    ULI_AIRGAP_SIGNING_KEY=deployment/keys/bundle_private.pem bash deployment/airgap/build_bundle.sh"
fi

echo "done: $ARCHIVE"
echo "verify on the target with: bash deployment/airgap/verify_bundle.sh $(basename "$ARCHIVE") <public-key.pem>"
echo "deploy on the target with: tar xzf $(basename "$ARCHIVE") -C <dest> && cd <dest> && ./LOAD.sh"
