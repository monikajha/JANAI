#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
PORT="${1:-9000}"
echo "Serving Jan demo UI + usage API at http://localhost:${PORT}"
PORT="$PORT" python3 app.py
