#!/bin/bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

date
cd "$ROOT_DIR"

if [ -f "$ROOT_DIR/.venv/bin/activate" ]; then
	. "$ROOT_DIR/.venv/bin/activate"
fi

API_BASE="${API_BASE:-http://localhost:8000}"

echo "Fetching latest forecast slots from ${API_BASE}/api/v1/forecasts/prices"
curl -fsS "${API_BASE}/api/v1/forecasts/prices?region=G&days=2&forecast_count=1&high_low=true" | cat
date

