import json
import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from sqlalchemy import func, select

from src.api.v1.deps import UnitOfWorkDep
from src.api.errors import http_error
from src.core.discord_notifications import send_discord_test_notification
from src.core.discord_runtime_config import (
    is_valid_discord_webhook_url,
    read_discord_runtime_config,
    write_discord_runtime_config,
)
from src.core.feed_health import get_feed_health
from src.core.ml_runtime_config import read_ml_runtime_config, write_ml_runtime_config
from src.core.settings import settings
from src.core.update_job_state import read_last_update_job_state, read_update_job_history
from src.ml.gpu_support import probe_xgboost_cuda
from src.repositories.sql_models import ExternalSystemContextORM, ForecastDataORM, ForecastORM, PriceHistoryORM
from src.schemas.diagnostics import (
    DiscordConfigRequest,
    DiscordConfigStatus,
    DiscordNotificationPreferences,
    DiscordTestResponse,
    ExternalSystemContextHealth,
    IngestPipelineHealth,
    PipelineTruthAudit,
    PipelineTruthIssue,
    MlGpuConfigRequest,
    MlGpuStatus,
    MlWriteModeRequest,
    MlWriteModeStatus,
    LatestForecastDiagnostics,
    LatestParitySummary,
    MlParityScorecard,
    ParityHistoryItem,
    ParityHistoryResponse,
    PipelineStageStatus,
    SourceCollectionStatus,
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


def _source_status(last_seen: datetime | None, now: datetime) -> str:
    if last_seen is None:
        return "missing"
    age_minutes = int((now - last_seen).total_seconds() // 60)
    if age_minutes <= 90:
        return "healthy"
    if age_minutes <= 180:
        return "aging"
    return "stale"


def _build_ml_gpu_status(*, force_test: bool = False) -> MlGpuStatus:
    config = read_ml_runtime_config()
    enabled = bool(config.get("gpu_enabled", False))
    probe = probe_xgboost_cuda(force=force_test)
    return MlGpuStatus(
        enabled=enabled,
        tested=probe.tested,
        compatible=probe.compatible,
        active=enabled and probe.compatible,
        gpu_name=probe.gpu_name,
        reason=probe.reason,
        xgboost_version=probe.xgboost_version,
        tested_at=probe.tested_at,
    )


def _resolve_ml_write_mode() -> str:
    config = read_ml_runtime_config()
    runtime_mode = config.get("write_mode")
    if runtime_mode in {"deterministic", "shadow", "ml"}:
        return runtime_mode
    return settings.ml_write_mode


def _build_discord_config_status() -> DiscordConfigStatus:
    config = read_discord_runtime_config()
    notifications = config.get("notifications") or {}
    webhook_url = str(config.get("webhook_url") or "").strip()
    return DiscordConfigStatus(
        enabled=bool(webhook_url),
        webhook_url=webhook_url or None,
        notifications=DiscordNotificationPreferences(
            update_started=bool(notifications.get("update_started", True)),
            update_success=bool(notifications.get("update_success", True)),
            update_failure=bool(notifications.get("update_failure", True)),
            parity_alert=bool(notifications.get("parity_alert", True)),
            gpu_alert=bool(notifications.get("gpu_alert", True)),
            daily_digest=bool(notifications.get("daily_digest", True)),
            pipeline_staleness=bool(notifications.get("pipeline_staleness", True)),
        ),
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
        update_ml_device_used=update_state.get("ml_device_used"),
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


@router.get("/ml-gpu-status", response_model=MlGpuStatus)
def ml_gpu_status() -> MlGpuStatus:
    return _build_ml_gpu_status()


@router.post("/ml-gpu-status", response_model=MlGpuStatus)
def set_ml_gpu_status(payload: MlGpuConfigRequest) -> MlGpuStatus:
    write_ml_runtime_config(gpu_enabled=payload.enabled)
    return _build_ml_gpu_status(force_test=True)


@router.get("/ml-write-mode", response_model=MlWriteModeStatus)
def ml_write_mode_status() -> MlWriteModeStatus:
    return MlWriteModeStatus(mode=_resolve_ml_write_mode())


@router.post("/ml-write-mode", response_model=MlWriteModeStatus)
def set_ml_write_mode(payload: MlWriteModeRequest) -> MlWriteModeStatus:
    write_ml_runtime_config(write_mode=payload.mode)
    return MlWriteModeStatus(mode=payload.mode)


@router.get("/discord-config", response_model=DiscordConfigStatus)
def discord_config() -> DiscordConfigStatus:
    return _build_discord_config_status()


@router.post("/discord-config", response_model=DiscordConfigStatus)
def set_discord_config(payload: DiscordConfigRequest) -> DiscordConfigStatus:
    webhook_url = (payload.webhook_url or "").strip()
    if webhook_url and not is_valid_discord_webhook_url(webhook_url):
        raise HTTPException(status_code=400, detail="Webhook URL must be a Discord webhook URL.")

    write_discord_runtime_config(
        webhook_url=webhook_url,
        notifications=payload.notifications.model_dump(),
    )
    return _build_discord_config_status()


@router.post("/discord-test", response_model=DiscordTestResponse)
def discord_test() -> DiscordTestResponse:
    sent, detail = send_discord_test_notification()
    if not sent:
        raise HTTPException(status_code=400, detail=detail)
    return DiscordTestResponse(sent=sent, detail=detail)


@router.get("/ingest-pipeline-health", response_model=IngestPipelineHealth)
def ingest_pipeline_health(uow: UnitOfWorkDep) -> IngestPipelineHealth:
    now = datetime.now(timezone.utc)
    since_24h = now - timedelta(hours=24)

    def forecast_field_source(key: str, label: str, field_name: str) -> SourceCollectionStatus:
        field = getattr(ForecastDataORM, field_name)

        total_rows = (
            uow.session.execute(select(func.count(ForecastDataORM.id)).where(field.is_not(None))).scalar_one() or 0
        )
        rows_24h = (
            uow.session.execute(
                select(func.count(ForecastDataORM.id)).where(
                    field.is_not(None),
                    ForecastDataORM.date_time >= since_24h,
                    ForecastDataORM.date_time <= now,
                )
            ).scalar_one()
            or 0
        )
        last_seen = uow.session.execute(
            select(func.max(ForecastDataORM.date_time)).where(
                field.is_not(None), ForecastDataORM.date_time <= now
            )
        ).scalar_one_or_none()
        recent_min, recent_max = uow.session.execute(
            select(func.min(field), func.max(field)).where(
                field.is_not(None),
                ForecastDataORM.date_time >= since_24h,
                ForecastDataORM.date_time <= now,
            )
        ).one()

        status = _source_status(last_seen, now)
        return SourceCollectionStatus(
            key=key,
            label=label,
            status=status,
            total_rows=int(total_rows),
            rows_24h=int(rows_24h),
            last_seen=last_seen.isoformat() if last_seen else None,
            recent_min=float(recent_min) if recent_min is not None else None,
            recent_max=float(recent_max) if recent_max is not None else None,
        )

    def price_history_source() -> SourceCollectionStatus:
        total_rows = uow.session.execute(select(func.count(PriceHistoryORM.id))).scalar_one() or 0
        rows_24h = (
            uow.session.execute(
                select(func.count(PriceHistoryORM.id)).where(
                    PriceHistoryORM.date_time >= since_24h,
                    PriceHistoryORM.date_time <= now,
                )
            ).scalar_one()
            or 0
        )
        last_seen = uow.session.execute(
            select(func.max(PriceHistoryORM.date_time)).where(PriceHistoryORM.date_time <= now)
        ).scalar_one_or_none()
        recent_min, recent_max = uow.session.execute(
            select(func.min(PriceHistoryORM.day_ahead), func.max(PriceHistoryORM.day_ahead)).where(
                PriceHistoryORM.date_time >= since_24h,
                PriceHistoryORM.date_time <= now,
            )
        ).one()
        status = _source_status(last_seen, now)
        return SourceCollectionStatus(
            key="nordpool_day_ahead",
            label="Nordpool day-ahead",
            status=status,
            total_rows=int(total_rows),
            rows_24h=int(rows_24h),
            last_seen=last_seen.isoformat() if last_seen else None,
            recent_min=float(recent_min) if recent_min is not None else None,
            recent_max=float(recent_max) if recent_max is not None else None,
        )

    sources = [
        forecast_field_source("neso_bm_wind", "NESO BM wind", "bm_wind"),
        forecast_field_source("neso_solar", "NESO solar", "solar"),
        forecast_field_source("neso_embedded_wind", "NESO embedded wind", "emb_wind"),
        forecast_field_source("elexon_demand", "Elexon/NESO demand", "demand"),
        forecast_field_source("openmeteo_temp", "Open-Meteo temp_2m", "temp_2m"),
        forecast_field_source("openmeteo_wind", "Open-Meteo wind_10m", "wind_10m"),
        forecast_field_source("openmeteo_rad", "Open-Meteo radiation", "rad"),
        price_history_source(),
    ]

    source_health_ok = [s for s in sources if s.status == "healthy"]
    all_sources_healthy = len(source_health_ok) == len(sources)

    forecast_count = uow.session.execute(select(func.count(ForecastORM.id))).scalar_one() or 0
    forecast_data_count = uow.session.execute(select(func.count(ForecastDataORM.id))).scalar_one() or 0
    price_count = uow.session.execute(select(func.count(PriceHistoryORM.id))).scalar_one() or 0

    scorecard = ml_parity_scorecard(30)
    parity = parity_last_summary()

    stages = [
        PipelineStageStatus(
            key="forecast_runs",
            label="1. Forecast runs created",
            status="ready" if forecast_count >= 2 else "warming",
            current=int(forecast_count),
            target=2,
            detail="Need at least two forecast runs to compare behavior over time.",
        ),
        PipelineStageStatus(
            key="feature_rows",
            label="2. Feature rows collected",
            status="ready" if forecast_data_count >= 50 else "warming",
            current=int(forecast_data_count),
            target=50,
            detail="Feature history must accumulate before parity scoring is meaningful.",
        ),
        PipelineStageStatus(
            key="price_rows",
            label="3. Price rows collected",
            status="ready" if price_count >= 50 else "warming",
            current=int(price_count),
            target=50,
            detail="Day-ahead price history is required for training targets.",
        ),
        PipelineStageStatus(
            key="parity_samples",
            label="4. ML parity sample window",
            status="ready" if scorecard.sample_size >= scorecard.window_size else "warming",
            current=scorecard.sample_size,
            target=scorecard.window_size,
            detail="Comparable ML vs deterministic runs are required for confidence scoring.",
        ),
        PipelineStageStatus(
            key="parity_report",
            label="5. Parity report available",
            status="ready" if parity.report_available else "warming",
            current=1 if parity.report_available else 0,
            target=1,
            detail="Parity report confirms the comparison pipeline has produced artifacts.",
        ),
    ]

    if scorecard.sample_size < scorecard.window_size:
        next_action = "Continue scheduled updates until parity sample window is filled."
    elif not parity.report_available:
        next_action = "Run parity gate or wait for parity report generation."
    elif not all_sources_healthy:
        next_action = "One or more upstream sources are stale; inspect source freshness rows."
    else:
        next_action = "All ingestion sources are healthy and pipeline stages are complete."

    return IngestPipelineHealth(
        generated_at=now.isoformat(),
        training_mode=scorecard.training_mode,
        next_action=next_action,
        all_sources_healthy=all_sources_healthy,
        healthy_source_count=len(source_health_ok),
        expected_source_count=len(sources),
        stages=stages,
        sources=sources,
    )


@router.get("/pipeline-truth-audit", response_model=PipelineTruthAudit)
def pipeline_truth_audit(uow: UnitOfWorkDep) -> PipelineTruthAudit:
    now = datetime.now(timezone.utc)

    update_state = read_last_update_job_state() or {}
    preferred_forecast_name = update_state.get("forecast_name")

    latest_forecast = None

    if preferred_forecast_name:
        latest_forecast = uow.session.execute(
            select(ForecastORM)
            .where(ForecastORM.name == preferred_forecast_name)
            .order_by(ForecastORM.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()

    if latest_forecast is None:
        latest_forecast = uow.session.execute(
            select(ForecastORM)
            .where(~ForecastORM.name.like("bundle::history-%"))
            .order_by(ForecastORM.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()

    if latest_forecast is None:
        return PipelineTruthAudit(
            generated_at=now.isoformat(),
            trust_level="low",
            latest_forecast_id=None,
            latest_forecast_created_at=None,
            latest_forecast_rows=0,
            latest_unique_slots=0,
            latest_duplicate_slots=0,
            latest_day_ahead_non_null_rows=0,
            latest_day_ahead_zero_rows=0,
            latest_day_ahead_zero_ratio=None,
            latest_data_last_seen=None,
            latest_data_freshness_minutes=None,
            issues=[
                PipelineTruthIssue(
                    code="no_operational_forecast",
                    severity="critical",
                    detail="No operational forecast run exists in storage.",
                )
            ],
        )

    latest_forecast_id = int(latest_forecast.id)
    latest_forecast_created_at = latest_forecast.created_at.isoformat()

    latest_rows = (
        uow.session.execute(
            select(func.count(ForecastDataORM.id)).where(ForecastDataORM.forecast_id == latest_forecast_id)
        ).scalar_one()
        or 0
    )
    latest_unique_slots = (
        uow.session.execute(
            select(func.count(func.distinct(ForecastDataORM.date_time))).where(
                ForecastDataORM.forecast_id == latest_forecast_id
            )
        ).scalar_one()
        or 0
    )
    latest_day_ahead_non_null_rows = (
        uow.session.execute(
            select(func.count(ForecastDataORM.id)).where(
                ForecastDataORM.forecast_id == latest_forecast_id,
                ForecastDataORM.day_ahead.is_not(None),
            )
        ).scalar_one()
        or 0
    )
    latest_day_ahead_zero_rows = (
        uow.session.execute(
            select(func.count(ForecastDataORM.id)).where(
                ForecastDataORM.forecast_id == latest_forecast_id,
                ForecastDataORM.day_ahead.is_not(None),
                ForecastDataORM.day_ahead == 0.0,
            )
        ).scalar_one()
        or 0
    )
    latest_data_last_seen = uow.session.execute(
        select(func.max(ForecastDataORM.date_time)).where(ForecastDataORM.forecast_id == latest_forecast_id)
    ).scalar_one_or_none()

    latest_duplicate_slots = max(0, int(latest_rows) - int(latest_unique_slots))

    latest_day_ahead_zero_ratio: float | None = None
    if latest_day_ahead_non_null_rows > 0:
        latest_day_ahead_zero_ratio = float(latest_day_ahead_zero_rows) / float(latest_day_ahead_non_null_rows)

    latest_data_freshness_minutes: int | None = None
    if latest_data_last_seen is not None:
        latest_data_freshness_minutes = max(
            0,
            int((now - latest_data_last_seen).total_seconds() // 60),
        )

    issues: list[PipelineTruthIssue] = []

    if latest_rows < 48:
        issues.append(
            PipelineTruthIssue(
                code="low_forecast_row_count",
                severity="critical",
                detail=f"Latest forecast has only {latest_rows} rows; expected at least 48.",
            )
        )

    if latest_duplicate_slots > 2:
        issues.append(
            PipelineTruthIssue(
                code="duplicate_slots",
                severity="high",
                detail=f"Latest forecast contains {latest_duplicate_slots} duplicate date_time slots.",
            )
        )

    if latest_day_ahead_non_null_rows == 0:
        issues.append(
            PipelineTruthIssue(
                code="missing_day_ahead_values",
                severity="critical",
                detail="Latest forecast has no non-null day_ahead values.",
            )
        )

    if latest_day_ahead_zero_ratio is not None and latest_day_ahead_zero_ratio > 0.2:
        issues.append(
            PipelineTruthIssue(
                code="excessive_zero_day_ahead",
                severity="high",
                detail=f"Latest forecast has {latest_day_ahead_zero_ratio:.1%} zero day_ahead values.",
            )
        )

    if latest_data_freshness_minutes is not None and latest_data_freshness_minutes > 240:
        issues.append(
            PipelineTruthIssue(
                code="stale_latest_forecast",
                severity="high",
                detail=f"Latest forecast data is {latest_data_freshness_minutes} minutes old.",
            )
        )

    if any(issue.severity == "critical" for issue in issues):
        trust_level = "low"
    elif issues:
        trust_level = "medium"
    else:
        trust_level = "high"

    return PipelineTruthAudit(
        generated_at=now.isoformat(),
        trust_level=trust_level,
        latest_forecast_id=latest_forecast_id,
        latest_forecast_created_at=latest_forecast_created_at,
        latest_forecast_rows=int(latest_rows),
        latest_unique_slots=int(latest_unique_slots),
        latest_duplicate_slots=int(latest_duplicate_slots),
        latest_day_ahead_non_null_rows=int(latest_day_ahead_non_null_rows),
        latest_day_ahead_zero_rows=int(latest_day_ahead_zero_rows),
        latest_day_ahead_zero_ratio=latest_day_ahead_zero_ratio,
        latest_data_last_seen=latest_data_last_seen.isoformat() if latest_data_last_seen else None,
        latest_data_freshness_minutes=latest_data_freshness_minutes,
        issues=issues,
    )


@router.get("/external-system-context-health", response_model=ExternalSystemContextHealth)
def external_system_context_health(uow: UnitOfWorkDep) -> ExternalSystemContextHealth:
    now = datetime.now(timezone.utc)
    since_24h = now - timedelta(hours=24)

    total_rows = uow.session.execute(select(func.count(ExternalSystemContextORM.id))).scalar_one() or 0
    rows_24h = (
        uow.session.execute(
            select(func.count(ExternalSystemContextORM.id)).where(ExternalSystemContextORM.date_time >= since_24h)
        ).scalar_one()
        or 0
    )
    latest_date_time = uow.session.execute(select(func.max(ExternalSystemContextORM.date_time))).scalar_one_or_none()

    carbon_rows = (
        uow.session.execute(
            select(func.count(ExternalSystemContextORM.id)).where(
                ExternalSystemContextORM.carbon_intensity.is_not(None)
            )
        ).scalar_one()
        or 0
    )
    fuel_rows = (
        uow.session.execute(
            select(func.count(ExternalSystemContextORM.id)).where(
                ExternalSystemContextORM.gas_mw.is_not(None),
                ExternalSystemContextORM.wind_mw.is_not(None),
                ExternalSystemContextORM.nuclear_mw.is_not(None),
            )
        ).scalar_one()
        or 0
    )
    interconnector_rows = (
        uow.session.execute(
            select(func.count(ExternalSystemContextORM.id)).where(
                ExternalSystemContextORM.interconnector_net_mw.is_not(None)
            )
        ).scalar_one()
        or 0
    )
    pumped_rows = (
        uow.session.execute(
            select(func.count(ExternalSystemContextORM.id)).where(
                ExternalSystemContextORM.pumped_storage_mw.is_not(None)
            )
        ).scalar_one()
        or 0
    )

    return ExternalSystemContextHealth(
        generated_at=now.isoformat(),
        total_rows=int(total_rows),
        rows_24h=int(rows_24h),
        latest_date_time=latest_date_time.isoformat() if latest_date_time else None,
        carbon_intensity_rows=int(carbon_rows),
        fuel_mix_rows=int(fuel_rows),
        interconnector_rows=int(interconnector_rows),
        pumped_storage_rows=int(pumped_rows),
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


@router.get("/feed-health")
def get_current_feed_health() -> dict:
    """Get health status for all external data feed sources.
    
    Returns per-feed metadata including:
    - last_successful_pull: when the feed last successfully ingested
    - records_received: count of records on last successful pull
    - status: 'healthy' | 'stale' | 'error' | 'unknown'
    - last_error: most recent error message, if any
    - error_count: total error count
    """
    return get_feed_health()
