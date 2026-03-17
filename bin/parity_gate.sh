#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE_TAG="${IMAGE_TAG:-agile-predict-backend:parity}"
LEGACY_BASE="${LEGACY_BASE:-http://host.docker.internal:8000}"
MIGRATED_BASE="${MIGRATED_BASE:-http://host.docker.internal:8010}"

PRED_TOLERANCE="${PRED_TOLERANCE:-0.25}"
MIN_COMMON_POINTS="${MIN_COMMON_POINTS:-1}"
MAX_MEAN_ABS_DIFF="${MAX_MEAN_ABS_DIFF:-0.15}"
MAX_MAX_ABS_DIFF="${MAX_MAX_ABS_DIFF:-0.25}"
MAX_P95_ABS_DIFF="${MAX_P95_ABS_DIFF:-0.2}"
REPORT_FILE="${REPORT_FILE:-shared/parity/last-report.json}"
ARCHIVE_DIR="${ARCHIVE_DIR:-shared/parity/history}"

mkdir -p "${ROOT_DIR}/$(dirname "${REPORT_FILE}")"
mkdir -p "${ROOT_DIR}/${ARCHIVE_DIR}"

docker build -f "${ROOT_DIR}/deploy/docker/backend.Dockerfile" -t "${IMAGE_TAG}" "${ROOT_DIR}"

docker run --rm \
  --add-host host.docker.internal:host-gateway \
  --entrypoint /bin/sh \
  -v "${ROOT_DIR}:/workspace" \
  "${IMAGE_TAG}" \
  -lc "python /workspace/shared/parity/api_parity_check.py \
    --legacy-base '${LEGACY_BASE}' \
    --migrated-base '${MIGRATED_BASE}' \
    --pred-tolerance '${PRED_TOLERANCE}' \
    --min-common-points '${MIN_COMMON_POINTS}' \
    --max-mean-abs-diff '${MAX_MEAN_ABS_DIFF}' \
    --max-max-abs-diff '${MAX_MAX_ABS_DIFF}' \
    --max-p95-abs-diff '${MAX_P95_ABS_DIFF}' \
    --report-file '/workspace/${REPORT_FILE}'"

if [ -f "${ROOT_DIR}/${REPORT_FILE}" ]; then
  timestamp="$(date -u +"%Y%m%dT%H%M%SZ")"
  cp "${ROOT_DIR}/${REPORT_FILE}" "${ROOT_DIR}/${ARCHIVE_DIR}/report-${timestamp}.json"
fi
