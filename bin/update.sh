#!/bin/bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

cd "$ROOT_DIR"

if [ -f "$ROOT_DIR/.venv/bin/activate" ]; then
	. "$ROOT_DIR/.venv/bin/activate"
fi

API_BASE="${API_BASE:-http://localhost:8000}"

echo "Triggering FastAPI update job at ${API_BASE}/api/v1/admin/jobs/run-update"
curl -fsS -X POST "${API_BASE}/api/v1/admin/jobs/run-update" \
  -H "Content-Type: application/json" | cat

echo
echo "Update job request submitted."

