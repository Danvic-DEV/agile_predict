import json
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException

from src.api.v1.deps import UnitOfWorkDep
from src.api.errors import http_error
from src.core.update_job_state import read_last_update_job_state, read_update_job_history
from src.schemas.diagnostics import (
    LatestForecastDiagnostics,
    MlParityScorecard,
    LatestParitySummary,
    ParityHistoryItem,
    ParityHistoryResponse,
)

router = APIRouter()
REPO_ROOT = Path(__file__).resolve().parents[5]
PARITY_REPORT_PATH = REPO_ROOT / "shared" / "parity" / "last-report.json"
PARITY_HISTORY_DIR = REPO_ROOT / "shared" / "parity" / "history"


def _relative_report_path(path: Path) -> str | None:
    try:
        return path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_report(path: Path) -> LatestParitySummary | None:
    if not path.exists():
        return None

    try:
        report_bytes = path.read_bytes()
        payload = json.loads(report_bytes.decode("utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    results = payload.get("results", []) or []
    data_stats_results = payload.get("data_stats_results", []) or []
    failures = payload.get("failures", []) or []
    thresholds = payload.get("thresholds", None)

    mean_abs_diffs: list[float] = []
    max_abs_diffs: list[float] = []
    p95_abs_diffs: list[float] = []
    common_points: list[int] = []

    for result in results:
        metrics = result.get("prediction_metrics", {}) or {}
        mean_abs = metrics.get("mean_abs_diff")
        max_abs = metrics.get("max_abs_diff")
        p95_abs = metrics.get("p95_abs_diff")
        common = metrics.get("common_points")

        if isinstance(mean_abs, (int, float)):
            mean_abs_diffs.append(float(mean_abs))
        if isinstance(max_abs, (int, float)):
            max_abs_diffs.append(float(max_abs))
        if isinstance(p95_abs, (int, float)):
            p95_abs_diffs.append(float(p95_abs))
        if isinstance(common, int):
            common_points.append(common)

    updated_at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
    report_sha256 = hashlib.sha256(report_bytes).hexdigest()

    return LatestParitySummary(
        report_available=True,
        all_passed=bool(payload.get("all_passed", False)),
        failure_count=len(failures),
        failures=[str(item) for item in failures],
        endpoint_count=len(results),
        data_stats_check_count=len(data_stats_results),
        min_common_points=min(common_points) if common_points else None,
        worst_mean_abs_diff=max(mean_abs_diffs) if mean_abs_diffs else None,
        worst_max_abs_diff=max(max_abs_diffs) if max_abs_diffs else None,
        worst_p95_abs_diff=max(p95_abs_diffs) if p95_abs_diffs else None,
        thresholds=thresholds if isinstance(thresholds, dict) else None,
        report_updated_at=updated_at,
        report_path=_relative_report_path(path),
        report_sha256=report_sha256,
    )


@router.get("/latest-summary", response_model=LatestForecastDiagnostics)
def latest_summary(uow: UnitOfWorkDep) -> LatestForecastDiagnostics:
    latest = uow.forecasts.list_latest(limit=1)
    if not latest:
        raise http_error(404, "no_forecasts_found", "No forecasts found.")

    forecast = latest[0]
    agile_all = uow.agile_data.list_for_forecast(forecast.id)
    agile_g = uow.agile_data.list_for_forecast(forecast.id, region="G")
    data_rows = uow.forecast_data.list_for_forecast(forecast.id, limit=2000)

    first_dt = data_rows[0].date_time.isoformat() if data_rows else None
    last_dt = data_rows[-1].date_time.isoformat() if data_rows else None

    day_ahead_values = [row.day_ahead for row in data_rows if row.day_ahead is not None]
    day_ahead_mean = round(sum(day_ahead_values) / len(day_ahead_values), 6) if day_ahead_values else None
    demand_mean = round(sum(row.demand for row in data_rows) / len(data_rows), 6) if data_rows else None
    update_state = read_last_update_job_state() or {}

    return LatestForecastDiagnostics(
        forecast_id=forecast.id,
        forecast_name=forecast.name,
        created_at=forecast.created_at,
        agile_points_total=len(agile_all),
        agile_points_region_g=len(agile_g),
        forecast_data_count=len(data_rows),
        forecast_data_first_date_time=first_dt,
        forecast_data_last_date_time=last_dt,
        day_ahead_mean=day_ahead_mean,
        demand_mean=demand_mean,
        update_source=update_state.get("source"),
        update_forecast_name=update_state.get("forecast_name"),
        update_records_written=update_state.get("records_written"),
        update_day_ahead_points=update_state.get("day_ahead_points"),
        update_source_updated_at=update_state.get("updated_at"),
        update_ingest_error=update_state.get("ingest_error"),
        update_raw_points=update_state.get("raw_points"),
        update_aligned_points=update_state.get("aligned_points"),
        update_interpolated_points=update_state.get("interpolated_points"),
        update_retries_used=update_state.get("retries_used"),
        update_ml_error=update_state.get("ml_error"),
        update_ml_training_rows=update_state.get("ml_training_rows"),
        update_ml_test_rows=update_state.get("ml_test_rows"),
        update_ml_cv_mean_rmse=update_state.get("ml_cv_mean_rmse"),
        update_ml_cv_stdev_rmse=update_state.get("ml_cv_stdev_rmse"),
        update_ml_feature_version=update_state.get("ml_feature_version"),
        update_ml_range_mode=update_state.get("ml_range_mode"),
        update_ml_candidate_points=update_state.get("ml_candidate_points"),
        update_ml_compare_mae=update_state.get("ml_compare_mae"),
        update_ml_compare_max_abs=update_state.get("ml_compare_max_abs"),
        update_ml_compare_p95_abs=update_state.get("ml_compare_p95_abs"),
        update_ml_write_mode=update_state.get("ml_write_mode"),
        training_mode=update_state.get("training_mode", False),
    )


@router.get("/ml-parity-scorecard", response_model=MlParityScorecard)
def ml_parity_scorecard(window_size: int = 30) -> MlParityScorecard:
    bounded_window = min(max(window_size, 5), 500)
    update_state = read_last_update_job_state() or {}
    history = read_update_job_history(limit=max(bounded_window * 4, bounded_window))

    comparable_runs = [
        row
        for row in history
        if isinstance(row.get("ml_compare_mae"), (int, float))
        and isinstance(row.get("ml_compare_p95_abs"), (int, float))
        and isinstance(row.get("ml_compare_max_abs"), (int, float))
    ]
    windowed = comparable_runs[-bounded_window:]

    if windowed:
        maes = [float(row["ml_compare_mae"]) for row in windowed]
        p95s = [float(row["ml_compare_p95_abs"]) for row in windowed]
        maxes = [float(row["ml_compare_max_abs"]) for row in windowed]
        rolling_mae = round(sum(maes) / len(maes), 6)
        rolling_p95 = round(sum(p95s) / len(p95s), 6)
        rolling_max = round(sum(maxes) / len(maxes), 6)
    else:
        rolling_mae = None
        rolling_p95 = None
        rolling_max = None

    target_mae = 8.0
    target_p95 = 20.0
    if rolling_mae is None or rolling_p95 is None:
        confidence = 0.0
    else:
        mae_score = max(0.0, min(1.0, 1.0 - (rolling_mae / target_mae)))
        p95_score = max(0.0, min(1.0, 1.0 - (rolling_p95 / target_p95)))
        coverage = min(1.0, len(windowed) / float(bounded_window))
        confidence = round((0.45 * mae_score + 0.45 * p95_score + 0.10 * coverage) * 100.0, 2)

    training_mode = bool(update_state.get("training_mode", True))
    configured_mode = update_state.get("ml_write_mode")
    effective_mode = "training" if training_mode else ("ml" if configured_mode == "ml" else "shadow")

    if confidence >= 80.0:
        label = "high"
    elif confidence >= 50.0:
        label = "medium"
    else:
        label = "low"

    return MlParityScorecard(
        report_available=len(windowed) > 0,
        training_mode=training_mode,
        configured_write_mode=configured_mode,
        effective_mode=effective_mode,
        sample_size=len(windowed),
        window_size=bounded_window,
        rolling_mae_vs_deterministic=rolling_mae,
        rolling_p95_abs_vs_deterministic=rolling_p95,
        rolling_max_abs_vs_deterministic=rolling_max,
        confidence_percent=confidence,
        confidence_label=label,
        latest_error=update_state.get("ml_error"),
    )


@router.get("/parity-last-summary", response_model=LatestParitySummary)
def parity_last_summary() -> LatestParitySummary:
    parsed = _parse_report(PARITY_REPORT_PATH)
    if parsed is None:
        return LatestParitySummary(
            report_available=False,
            all_passed=None,
            failure_count=None,
            failures=[],
            endpoint_count=None,
            data_stats_check_count=None,
            min_common_points=None,
            worst_mean_abs_diff=None,
            worst_max_abs_diff=None,
            worst_p95_abs_diff=None,
            thresholds=None,
            report_updated_at=None,
            report_path=None,
            report_sha256=None,
        )

    return parsed


@router.get("/parity-history", response_model=ParityHistoryResponse)
def parity_history(
    limit: int = 10,
    offset: int = 0,
    status: Literal["pass", "fail"] | None = None,
    since: str | None = None,
    until: str | None = None,
) -> ParityHistoryResponse:
    bounded_limit = min(max(limit, 1), 100)
    bounded_offset = max(offset, 0)
    items: list[ParityHistoryItem] = []
    try:
        since_dt = _parse_iso_datetime(since)
        until_dt = _parse_iso_datetime(until)
    except ValueError as exc:
        raise http_error(422, "invalid_iso_datetime_filter", "Invalid ISO datetime filter.", exc) from exc

    if PARITY_HISTORY_DIR.exists():
        report_paths = sorted(
            PARITY_HISTORY_DIR.glob("*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for path in report_paths:
            parsed = _parse_report(path)
            if parsed is None:
                continue
            items.append(
                ParityHistoryItem(
                    report_available=parsed.report_available,
                    all_passed=parsed.all_passed,
                    failure_count=parsed.failure_count,
                    report_updated_at=parsed.report_updated_at,
                    report_path=parsed.report_path,
                    report_sha256=parsed.report_sha256,
                )
            )

    if not items:
        latest = parity_last_summary()
        if latest.report_available:
            items.append(
                ParityHistoryItem(
                    report_available=latest.report_available,
                    all_passed=latest.all_passed,
                    failure_count=latest.failure_count,
                    report_updated_at=latest.report_updated_at,
                    report_path=latest.report_path,
                    report_sha256=latest.report_sha256,
                )
            )

    filtered_items: list[ParityHistoryItem] = []
    for item in items:
        if status == "pass" and item.all_passed is not True:
            continue
        if status == "fail" and item.all_passed is not False:
            continue

        updated_at_dt = _parse_iso_datetime(item.report_updated_at)
        if since_dt is not None and updated_at_dt is not None and updated_at_dt < since_dt:
            continue
        if until_dt is not None and updated_at_dt is not None and updated_at_dt > until_dt:
            continue

        filtered_items.append(item)

    total = len(filtered_items)
    paged_items = filtered_items[bounded_offset : bounded_offset + bounded_limit]
    return ParityHistoryResponse(
        items=paged_items,
        total=total,
        limit=bounded_limit,
        offset=bounded_offset,
        returned=len(paged_items),
    )
