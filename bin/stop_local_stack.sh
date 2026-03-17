#!/usr/bin/env bash
set -euo pipefail

docker rm -f agile-predict-app-dev agile-predict-frontend-dev agile-predict-backend-dev agile-predict-postgres >/dev/null 2>&1 || true
