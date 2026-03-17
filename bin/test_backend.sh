#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE_TAG="${IMAGE_TAG:-agile-predict-backend:test}"

# This value is only used to satisfy startup config validation during imports.
DATABASE_URL="${DATABASE_URL:-postgresql+psycopg://user:pass@localhost:5432/testdb}"

docker build -f "${ROOT_DIR}/deploy/docker/backend.Dockerfile" -t "${IMAGE_TAG}" "${ROOT_DIR}"

docker run --rm \
  --entrypoint /bin/sh \
  -e DB_MODE=external \
  -e DATABASE_URL="${DATABASE_URL}" \
  -e PYTHONPATH=/app/backend \
  "${IMAGE_TAG}" \
  -lc "cd /app/backend && pytest tests -q"
