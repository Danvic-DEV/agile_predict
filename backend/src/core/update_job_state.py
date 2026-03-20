from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.core.settings import CONFIG_DIR

UPDATE_JOB_STATE_PATH = Path(CONFIG_DIR) / "update-job-last-run.json"
UPDATE_JOB_HISTORY_PATH = Path(CONFIG_DIR) / "update-job-history.jsonl"


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
    ml_error: str | None = None,
    ml_training_rows: int | None = None,
    ml_test_rows: int | None = None,
    ml_cv_mean_rmse: float | None = None,
    ml_cv_stdev_rmse: float | None = None,
    ml_feature_version: str | None = None,
    ml_range_mode: str | None = None,
    ml_candidate_points: int | None = None,
    ml_compare_mae: float | None = None,
    ml_compare_max_abs: float | None = None,
    ml_compare_p95_abs: float | None = None,
    ml_write_mode: str | None = None,
    ml_device_used: str | None = None,
    training_mode: bool = False,
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
        "ml_error": ml_error,
        "ml_training_rows": ml_training_rows,
        "ml_test_rows": ml_test_rows,
        "ml_cv_mean_rmse": ml_cv_mean_rmse,
        "ml_cv_stdev_rmse": ml_cv_stdev_rmse,
        "ml_feature_version": ml_feature_version,
        "ml_range_mode": ml_range_mode,
        "ml_candidate_points": ml_candidate_points,
        "ml_compare_mae": ml_compare_mae,
        "ml_compare_max_abs": ml_compare_max_abs,
        "ml_compare_p95_abs": ml_compare_p95_abs,
        "ml_write_mode": ml_write_mode,
        "ml_device_used": ml_device_used,
        "training_mode": training_mode,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    try:
        UPDATE_JOB_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        UPDATE_JOB_STATE_PATH.write_text(json.dumps(payload), encoding="utf-8")
        with UPDATE_JOB_HISTORY_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload) + "\n")
    except OSError:
        return


def read_last_update_job_state() -> dict[str, Any] | None:
    if not UPDATE_JOB_STATE_PATH.exists():
        return None

    try:
        return json.loads(UPDATE_JOB_STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def read_update_job_history(limit: int = 200) -> list[dict[str, Any]]:
    if not UPDATE_JOB_HISTORY_PATH.exists():
        return []

    try:
        lines = UPDATE_JOB_HISTORY_PATH.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []

    rows: list[dict[str, Any]] = []
    for line in lines[-max(1, limit) :]:
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            rows.append(parsed)
    return rows
