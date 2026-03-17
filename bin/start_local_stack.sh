#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_CONTAINER="${APP_CONTAINER:-agile-predict-app-dev}"
CONFIG_DIR="${CONFIG_DIR:-${ROOT_DIR}/runtime-config}"
ENV_PATH="${CONFIG_DIR}/.env"
DEFAULT_ENV_PATH="${ROOT_DIR}/deploy/docker/default.env"

mkdir -p "${CONFIG_DIR}"
if [ ! -f "${ENV_PATH}" ]; then
  cp "${DEFAULT_ENV_PATH}" "${ENV_PATH}"
fi

ENV_PATH_PY="${ENV_PATH}" python3 - <<'PY'
import os
from pathlib import Path

env_path = Path(os.environ["ENV_PATH_PY"])
content = env_path.read_text(encoding="utf-8").splitlines()
updates = {
  "DB_MODE": "local",
  "DATABASE_URL": "postgresql+psycopg://postgres:postgres@127.0.0.1:5432/agile_predict",
  "CORS_ALLOWED_ORIGINS": "http://localhost:8000,http://127.0.0.1:8000,http://localhost:5173,http://127.0.0.1:5173",
  "AUTO_BOOTSTRAP_ON_STARTUP": "true",
}
seen = set()
out = []
for line in content:
  if "=" in line and not line.lstrip().startswith("#"):
    key, _ = line.split("=", 1)
    if key in updates:
      out.append(f"{key}={updates[key]}")
      seen.add(key)
      continue
  out.append(line)
for key, value in updates.items():
  if key not in seen:
    out.append(f"{key}={value}")
env_path.write_text("\n".join(out) + "\n", encoding="utf-8")
PY

docker rm -f "${APP_CONTAINER}" agile-predict-frontend-dev agile-predict-backend-dev agile-predict-postgres >/dev/null 2>&1 || true

docker build -f "${ROOT_DIR}/deploy/docker/backend.Dockerfile" -t agile-predict:dev "${ROOT_DIR}" >/dev/null

docker run -d \
  --name "${APP_CONTAINER}" \
  -p 8000:8000 \
  -v "${CONFIG_DIR}:/config" \
  agile-predict:dev >/dev/null

echo "App: http://localhost:8000"
