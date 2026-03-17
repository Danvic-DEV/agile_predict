from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.core.settings import CONFIG_DIR

UPDATE_JOB_STATE_PATH = Path(CONFIG_DIR) / "update-job-last-run.json"


def write_last_update_job_state(
    *,
    source: str,
    forecast_name: str,
    records_written: int,
    day_ahead_points: int,
    ingest_error: str | None = None,
    raw_points: int | None = None,
    aligned_points: int | None = None,
    interpolated_points: int | None = None,
    retries_used: int | None = None,
) -> None:
    payload: dict[str, Any] = {
        "source": source,
        "forecast_name": forecast_name,
        "records_written": int(records_written),
        "day_ahead_points": int(day_ahead_points),
        "ingest_error": ingest_error,
        "raw_points": raw_points,
        "aligned_points": aligned_points,
        "interpolated_points": interpolated_points,
        "retries_used": retries_used,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    try:
        UPDATE_JOB_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        UPDATE_JOB_STATE_PATH.write_text(json.dumps(payload), encoding="utf-8")
    except OSError:
        return


def read_last_update_job_state() -> dict[str, Any] | None:
    if not UPDATE_JOB_STATE_PATH.exists():
        return None

    try:
        return json.loads(UPDATE_JOB_STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
