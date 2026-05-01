#!/usr/bin/env bash
# One-command local bootstrap for JAN demo.
#
# Starts Keycloak (Docker Compose), checks JAN API health, then runs demo UI/backend.
#
# Usage:
#   ./scripts/bootstrap-local.sh
#
# Environment variables:
#   JAN_API_BASE - JAN API base URL (default: http://localhost:8000)
#   UI_PORT      - Demo UI port (default: 9000)

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
JAN_API_BASE="${JAN_API_BASE:-http://localhost:8000}"
UI_PORT="${UI_PORT:-9000}"

echo "[1/3] Starting local Keycloak stack..."
(
  cd "$ROOT_DIR/infra/keycloak"
  docker-compose up -d
)

echo "[2/3] Checking JAN API health at ${JAN_API_BASE}..."
MAX_TRIES=20
OK=0
for i in $(seq 1 "$MAX_TRIES"); do
  if curl -sf "${JAN_API_BASE}/health" >/dev/null 2>&1 || curl -sf "${JAN_API_BASE}/v1/models" >/dev/null 2>&1; then
    OK=1
    break
  fi
  echo "  - JAN API not ready yet (attempt ${i}/${MAX_TRIES})"
  sleep 2
done

if [[ "$OK" -ne 1 ]]; then
  echo "WARN: Could not verify JAN API readiness at ${JAN_API_BASE}."
  echo "      Proceeding to start demo UI anyway."
fi

echo "[3/3] Starting JAN demo UI/backend on port ${UI_PORT}..."
exec "$ROOT_DIR/scripts/jan-demo-ui/start.sh" "$UI_PORT"
