#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPORT_FILE="${REPORT_FILE:-shared/parity/last-report.json}"

if [[ -z "${LEGACY_BASE:-}" || -z "${MIGRATED_BASE:-}" ]]; then
  echo "LEGACY_BASE and MIGRATED_BASE must be set" >&2
  exit 2
fi

"${ROOT_DIR}/bin/parity_gate.sh"

python - <<'PY'
import json
from pathlib import Path

report_path = Path("shared/parity/last-report.json")
if not report_path.exists():
    raise SystemExit("Parity report was not generated")

payload = json.loads(report_path.read_text(encoding="utf-8"))
all_passed = bool(payload.get("all_passed", False))
failures = payload.get("failures", []) or []
results = payload.get("results", []) or []

print(f"parity_all_passed={all_passed}")
print(f"endpoint_checks={len(results)}")
print(f"failures={len(failures)}")

if not all_passed:
    for failure in failures:
        print(f" - {failure}")
    raise SystemExit(1)
PY

echo "Parity signoff checks passed."
