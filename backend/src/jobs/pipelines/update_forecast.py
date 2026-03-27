import logging
import math
from datetime import datetime, timedelta, timezone

import pandas as pd

from src.core.discord_notifications import (
    PARITY_ALERT_THRESHOLDS,
    clear_pipeline_staleness_alert_state,
    send_daily_digest_notification,
    send_gpu_alert_notification,
    send_parity_alert_notification,
    send_pipeline_staleness_alert_notification,
    send_update_success_notification,
)
from src.core.ml_runtime_config import read_ml_runtime_config
from src.core.settings import settings
from src.core.update_job_state import write_last_update_job_state
from src.domain.bootstrap_bundle import (
    BootstrapBundleConfig,
    HistoryForecastFeatureRow,
    prune_old_forecasts,
    prune_update_job_forecasts,
    write_bootstrap_bundle,
    write_history_forecast,
)
from src.domain.forecast_pipeline import ForecastRunResult, run_forecast_pipeline
from src.ml.ingest.grid_weather import fetch_grid_weather_features, fetch_live_forecast_features
from src.ml.ingest.nordpool import fetch_day_ahead_prices
from src.ml.ingest.octopus_agile import fetch_agile_prices_all_regions
from src.ml.ingest.gas_sap import fetch_gas_sap, to_orm_rows
from src.ml.ingest.system_context import fetch_system_context_features
from src.ml.gpu_support import probe_xgboost_cuda
from src.ml.parity.day_ahead_xgb import check_ml_training_readiness, run_ml_day_ahead_forecast
from src.repositories.unit_of_work import UnitOfWork

log = logging.getLogger(__name__)


def _align_to_half_hour(dt: datetime) -> datetime:
    aligned = dt.astimezone(timezone.utc).replace(second=0, microsecond=0)
    return aligned.replace(minute=0 if aligned.minute < 30 else 30)


def _diff_metrics(reference: tuple[float, ...], candidate: tuple[float, ...]) -> tuple[float, float, float] | None:
    if not reference or not candidate:
        return None
    n = min(len(reference), len(candidate))
    if n == 0:
        return None

    diffs = sorted(abs(reference[i] - candidate[i]) for i in range(n))
    mae = sum(diffs) / n
    max_abs = diffs[-1]
    p95_idx = min(n - 1, int(round(0.95 * (n - 1))))
    p95 = diffs[p95_idx]
    return float(mae), float(max_abs), float(p95)


def _zero_ratio(values: tuple[float, ...], *, epsilon: float = 1e-6) -> float:
    if not values:
        return 0.0
    zeros = sum(1 for value in values if abs(float(value)) <= epsilon)
    return float(zeros) / float(len(values))


def _is_degenerate_ml_output(values: tuple[float, ...]) -> bool:
    if not values:
        return True
    unique_rounded = len({round(float(value), 4) for value in values})
    if unique_rounded <= 2:
        return True
    return _zero_ratio(values) >= 0.95


def run_update_forecast_job(uow: UnitOfWork) -> ForecastRunResult:
    pipeline = run_forecast_pipeline(fallback_points=settings.auto_bootstrap_points)
    deterministic_values = pipeline.day_ahead_values
    day_ahead_values = deterministic_values
    day_ahead_low_values: tuple[float, ...] | None = None
    day_ahead_high_values: tuple[float, ...] | None = None

    source = pipeline.source
    ml_error: str | None = None
    ml_training_rows: int | None = None
    ml_test_rows: int | None = None
    ml_cv_mean_rmse: float | None = None
    ml_cv_stdev_rmse: float | None = None
    ml_feature_version: str | None = None
    ml_range_mode: str | None = None
    ml_candidate_points: int | None = None
    ml_compare_mae: float | None = None
    ml_compare_max_abs: float | None = None
    ml_compare_p95_abs: float | None = None
    ml_device_used: str | None = None
    gpu_config = read_ml_runtime_config()
    gpu_requested = bool(gpu_config.get("gpu_enabled", False))
    gpu_probe = probe_xgboost_cuda()
    gpu_active = gpu_requested and gpu_probe.compatible

    if gpu_requested and not gpu_probe.compatible:
        gpu_reason = gpu_probe.reason or "GPU test failed"
        ml_error = f"GPU requested but unavailable: {gpu_reason}"

    configured_ml_write_mode = gpu_config.get("write_mode") or settings.ml_write_mode
    ml_ready, ml_ready_reason = check_ml_training_readiness(uow=uow)
    ml_write_mode = configured_ml_write_mode
    training_mode = ml_write_mode == "deterministic"
    if training_mode and ml_ready_reason is not None:
        ml_error = ml_ready_reason

    forecast_points = len(day_ahead_values)
    forecast_horizon_days = max(1, math.ceil(forecast_points / 48) + 1)
    forecast_anchor = _align_to_half_hour(datetime.now(timezone.utc))
    forward_features_df = fetch_live_forecast_features(
        forecast_days=forecast_horizon_days,
        now=forecast_anchor,
    )
    forward_features_df = forward_features_df.sort_index()
    forward_features_df.index = pd.to_datetime(forward_features_df.index, utc=True)
    forward_features_df = forward_features_df[forward_features_df.index >= pd.Timestamp(forecast_anchor)]

    available_feature_points = len(forward_features_df)
    if available_feature_points == 0:
        raise RuntimeError("insufficient forward feature rows for forecast horizon: have=0")

    forward_feature_warning: str | None = None
    if available_feature_points < forecast_points:
        missing_points = forecast_points - available_feature_points
        missing_days = missing_points / 48.0
        forward_feature_warning = (
            f"insufficient forward feature rows for full horizon: "
            f"have={available_feature_points} need={forecast_points} "
            f"(missing={missing_points} slots, {missing_days:.2f} days)"
        )
        if not settings.allow_partial_forecast_horizon:
            raise RuntimeError(forward_feature_warning)

        log.warning(
            "Partial forecast horizon enabled; publishing degraded forecast with real rows only: %s",
            forward_feature_warning,
        )
        forecast_points = available_feature_points
        deterministic_values = tuple(deterministic_values[:forecast_points])
        day_ahead_values = deterministic_values

    live_feature_frame = forward_features_df.iloc[:forecast_points].copy()

    if ml_write_mode in {"ml", "shadow"}:
        try:
            ml_output = run_ml_day_ahead_forecast(
                uow=uow,
                point_count=forecast_points,
                future_feature_frame=live_feature_frame,
                use_gpu=gpu_active,
            )
        except Exception as exc:
            ml_error = str(exc)
            ml_device_used = "cpu"  # Fallback to cpu on error
            if ml_write_mode == "ml":
                raise RuntimeError(f"ML forecast failed with fallback disabled: {exc}") from exc
            elif not settings.allow_ml_fallback:
                raise RuntimeError(f"ML forecast failed with fallback disabled: {exc}") from exc
            else:
                day_ahead_values = deterministic_values
                day_ahead_low_values = None
                day_ahead_high_values = None
                source = pipeline.source
        else:
            ml_candidate_points = len(ml_output.day_ahead_values)
            ml_device_used = "gpu" if gpu_active else "cpu"

            ml_training_rows = ml_output.training_rows
            ml_test_rows = ml_output.test_rows
            ml_cv_mean_rmse = ml_output.cv_mean_rmse
            ml_cv_stdev_rmse = ml_output.cv_stdev_rmse
            ml_feature_version = "|".join(ml_output.feature_columns)
            ml_range_mode = ml_output.range_mode

            diffs = _diff_metrics(deterministic_values, ml_output.day_ahead_values)
            if diffs is not None:
                ml_compare_mae, ml_compare_max_abs, ml_compare_p95_abs = diffs

            if ml_write_mode == "ml":
                if _is_degenerate_ml_output(ml_output.day_ahead_values):
                    reason = (
                        f"ML output rejected as degenerate: zero_ratio={_zero_ratio(ml_output.day_ahead_values):.1%}, "
                        f"points={len(ml_output.day_ahead_values)}"
                    )
                    ml_error = reason
                    raise RuntimeError(reason)
                else:
                    day_ahead_values = ml_output.day_ahead_values
                    day_ahead_low_values = ml_output.day_ahead_low_values
                    day_ahead_high_values = ml_output.day_ahead_high_values
                    source = "ml"
            else:
                day_ahead_values = deterministic_values
                day_ahead_low_values = None
                day_ahead_high_values = None
                source = f"shadow:{pipeline.source}"
    else:
        ml_device_used = "cpu"  # Deterministic pipeline uses cpu
        day_ahead_values = deterministic_values
        day_ahead_low_values = None
        day_ahead_high_values = None
        source = pipeline.source

    partial_horizon_warning: str | None = None
    if forward_feature_warning is not None:
        partial_horizon_warning = forward_feature_warning
        source = f"{source}:partial-horizon"

    feature_rows = [
        HistoryForecastFeatureRow(
            date_time=ts.to_pydatetime(),
            bm_wind=float(row["bm_wind"]),
            solar=float(row["solar"]),
            emb_wind=float(row["emb_wind"]),
            demand=float(row["demand"]),
            temp_2m=float(row["temp_2m"]),
            wind_10m=float(row["wind_10m"]),
            rad=float(row["rad"]),
            day_ahead=None,
        )
        for ts, row in live_feature_frame.iterrows()
    ]

    # Generate timestamped forecast name for multi-forecast tracking
    now_utc = datetime.now(timezone.utc)
    forecast_timestamp = now_utc.strftime("%Y-%m-%d-%H:%M")
    forecast_name = f"bundle::update-job-{forecast_timestamp}"

    result = write_bootstrap_bundle(
        uow=uow,
        config=BootstrapBundleConfig(
            points=forecast_points,
            forecast_name=forecast_name,
            replace_existing=False,
            regions=tuple(settings.bootstrap_regions_list),
            day_ahead_values=day_ahead_values,
            day_ahead_low_values=day_ahead_low_values,
            day_ahead_high_values=day_ahead_high_values,
            feature_rows=tuple(feature_rows),
            forecast_mean=ml_cv_mean_rmse,
            forecast_stdev=ml_cv_stdev_rmse,
            write_agile_data=True,
        ),
    )

    # Prune old update-job forecasts, keeping only the last 10
    prune_update_job_forecasts(uow=uow, keep_count=10)

    records_written = result.forecast_data_points_written + result.agile_data_points_written
    output = ForecastRunResult(
        records_written=records_written,
        forecast_name=result.forecast_name,
        source=source,
        day_ahead_points=forecast_points,
    )

    # ------------------------------------------------------------------
    # Step 2: fetch real grid+weather features and write a dated history
    # forecast row so the ML trainer accumulates real training data.
    # Failures here are non-fatal — we log and continue.
    # ------------------------------------------------------------------
    grid_error: str | None = None
    history_records_written = 0
    try:
        features_df = fetch_grid_weather_features(lookback_days=62)

        # Build feature rows: align the feature DataFrame to 30min UTC slots
        # and tag each row with the actual Nordpool day-ahead price if available.
        # We use the pipeline's known prices for the current window only; all
        # historical slots get day_ahead=None (will be joined from PriceHistoryORM).
        feature_rows: list[HistoryForecastFeatureRow] = []
        for ts, row in features_df.iterrows():
            ts_utc = pd.Timestamp(ts).tz_convert("UTC") if getattr(ts, "tzinfo", None) else pd.Timestamp(ts, tz="UTC")
            feature_rows.append(
                HistoryForecastFeatureRow(
                    date_time=ts_utc.to_pydatetime(),
                    bm_wind=float(row["bm_wind"]),
                    solar=float(row["solar"]),
                    emb_wind=float(row["emb_wind"]),
                    demand=float(row["demand"]),
                    temp_2m=float(row["temp_2m"]),
                    wind_10m=float(row["wind_10m"]),
                    rad=float(row["rad"]),
                    day_ahead=None,
                )
            )

        if feature_rows:
            hist_result = write_history_forecast(
                uow=uow,
                feature_rows=feature_rows,
                now=None,
                regions=tuple(settings.bootstrap_regions_list),
                forecast_mean=ml_cv_mean_rmse,
                forecast_stdev=ml_cv_stdev_rmse,
            )
            history_records_written = (
                hist_result.forecast_data_points_written + hist_result.agile_data_points_written
            )
            log.info("History forecast written: %s rows", history_records_written)
    except Exception as exc:  # noqa: BLE001
        grid_error = str(exc)
        log.warning("Grid/weather feature fetch failed (non-fatal): %s", exc)

    # ------------------------------------------------------------------
    # Step 3: upsert today's Nordpool prices into PriceHistoryORM so the
    # ML join has actual price targets to train against.
    # ------------------------------------------------------------------
    price_upsert_count = 0
    try:
        now_utc = datetime.now(timezone.utc)
        raw_prices = fetch_day_ahead_prices(now=now_utc)
        if raw_prices:
            price_rows = [
                {
                    "date_time": dt,
                    "day_ahead": float(price),
                    "agile": float(price),
                }
                for dt, price in raw_prices.items()
            ]
            price_upsert_count = uow.price_history_writes.upsert_many(price_rows)
            log.info("Upserted %s Nordpool price rows into PriceHistoryORM", price_upsert_count)
    except Exception as exc:  # noqa: BLE001
        log.warning("Nordpool price upsert failed (non-fatal): %s", exc)

    # ------------------------------------------------------------------
    # Step 3a: fetch and collect actual released Agile prices from Octopus
    # for all 15 UK regions. These are the retroactively measured prices
    # used for accuracy comparison vs forecasts and ML training.
    # 
    # Default: Last 365 days (1 year) to build substantial training dataset
    # Use /admin-jobs/backfill-agile-prices for multi-year deep backfill
    # ------------------------------------------------------------------
    agile_actual_upsert_count = 0
    try:
        now_utc = datetime.now(timezone.utc)
        from_date = now_utc - timedelta(days=365)  # 1 year lookback
        # Agile prices are released ~4pm daily covering up to 10:30pm the next
        # day, so we must extend to_date to capture those forward-released prices.
        to_date = now_utc + timedelta(days=2)
        agile_prices_by_region = fetch_agile_prices_all_regions(
            from_date=from_date,
            to_date=to_date,
            timeout=20,
        )

        # Flatten to upsert rows: one row per (date_time, region, agile_actual) triplet
        agile_rows = []
        total_prices = 0
        for region, prices_dict in agile_prices_by_region.items():
            for dt, price in prices_dict.items():
                agile_rows.append(
                    {
                        "date_time": dt,
                        "region": region,
                        "agile_actual": float(price),
                    }
                )
                total_prices += 1

        if agile_rows:
            agile_actual_upsert_count = uow.agile_actual_writes.upsert_many(agile_rows)
            log.info(
                "Upserted %s Agile actual price rows (%d total prices, %d regions)",
                agile_actual_upsert_count,
                total_prices,
                len(agile_prices_by_region),
            )
        else:
            log.warning("No Agile prices fetched from Octopus API")
    except Exception as exc:  # noqa: BLE001
        log.error("Agile actual price ingest failed (fail-closed): %s", exc)
        raise

    # ------------------------------------------------------------------
    # Step 3b: patch forecast agile_pred values with published Octopus prices.
    # Once Octopus releases tomorrow's Agile tariff (~16:00 UK time), every
    # forecast slot that has a published price should show that price as the
    # prediction — not the Nordpool-derived transform.  This is non-fatal.
    # ------------------------------------------------------------------
    try:
        from sqlalchemy import select
        from src.repositories.sql_models import ForecastORM as _ForecastORM
        _stmt = (
            select(_ForecastORM.id)
            .where(_ForecastORM.name.like("bundle::update-job-%"))
            .order_by(_ForecastORM.created_at.desc())
            .limit(10)
        )
        active_forecast_ids = list(uow.session.execute(_stmt).scalars().all())
        patched = uow.agile_data_writes.patch_pred_from_actuals(active_forecast_ids)
        log.info("Patched %s forecast slots with published Octopus prices", patched)
    except Exception as exc:  # noqa: BLE001
        log.warning("Agile pred patch from actuals failed (non-fatal): %s", exc)

    # ------------------------------------------------------------------
    # Step 3c: ingest additional system-context feeds for future Wave A
    # ML improvements (carbon intensity, fuel mix/interconnectors).
    # This is non-fatal and currently used for diagnostics + future training.
    # ------------------------------------------------------------------
    external_context_upsert_count = 0
    try:
        context_df = fetch_system_context_features(lookback_days=3)
        if not context_df.empty:
            context_rows = []
            for ts, row in context_df.iterrows():
                ts_utc = pd.Timestamp(ts).tz_convert("UTC") if getattr(ts, "tzinfo", None) else pd.Timestamp(ts, tz="UTC")
                context_rows.append(
                    {
                        "date_time": ts_utc.to_pydatetime(),
                        "carbon_intensity": float(row["carbon_intensity"]),
                        "gas_mw": float(row["gas_mw"]),
                        "wind_mw": float(row["wind_mw"]),
                        "nuclear_mw": float(row["nuclear_mw"]),
                        "pumped_storage_mw": float(row["pumped_storage_mw"]),
                        "interconnector_net_mw": float(row["interconnector_net_mw"]),
                    }
                )

            external_context_upsert_count = uow.external_system_context_writes.upsert_many(context_rows)
            log.info("Upserted %s external system context rows", external_context_upsert_count)
    except Exception as exc:  # noqa: BLE001
        log.warning("External system context ingest failed (non-fatal): %s", exc)

    # ------------------------------------------------------------------
    # Step 3d: fetch today's National Gas SAP and upsert into GasSapORM.
    # Published ~12:40 UK time, available before the 16:15 forecast window.
    # Non-fatal — a missing SAP value causes the ML model to treat it as
    # NaN (XGBoost handles missing features gracefully).
    # ------------------------------------------------------------------
    try:
        today_str = datetime.now(timezone.utc).date().isoformat()
        gas_sap_by_date = fetch_gas_sap(today_str, today_str)
        if gas_sap_by_date:
            gas_sap_orm_rows = to_orm_rows(gas_sap_by_date)
            gas_sap_upsert_count = uow.gas_sap_writes.upsert_many(gas_sap_orm_rows)
            log.info("Upserted %s gas SAP rows", gas_sap_upsert_count)
    except Exception as exc:  # noqa: BLE001
        log.warning("Gas SAP ingest failed (non-fatal): %s", exc)

    # ------------------------------------------------------------------
    # Step 4: prune history forecasts older than 730 days (keeps 2 years for year-over-year seasonality).
    # ------------------------------------------------------------------
    try:
        pruned = prune_old_forecasts(uow=uow, max_age_days=730)
        if pruned:
            log.info("Pruned %s old history forecast rows", pruned)
    except Exception as exc:  # noqa: BLE001
        log.warning("Forecast pruning failed (non-fatal): %s", exc)

    write_last_update_job_state(
        source=source,
        forecast_name=result.forecast_name,
        records_written=records_written,
        day_ahead_points=len(day_ahead_values),
        ingest_error=pipeline.ingest_error,
        raw_points=pipeline.raw_points,
        aligned_points=pipeline.aligned_points,
        interpolated_points=pipeline.interpolated_points,
        retries_used=pipeline.retries_used,
        ml_error=ml_error,
        partial_horizon_warning=partial_horizon_warning,
        ml_training_rows=ml_training_rows,
        ml_test_rows=ml_test_rows,
        ml_cv_mean_rmse=ml_cv_mean_rmse,
        ml_cv_stdev_rmse=ml_cv_stdev_rmse,
        ml_feature_version=ml_feature_version,
        ml_range_mode=ml_range_mode,
        ml_candidate_points=ml_candidate_points,
        ml_compare_mae=ml_compare_mae,
        ml_compare_max_abs=ml_compare_max_abs,
        ml_compare_p95_abs=ml_compare_p95_abs,
        ml_write_mode=ml_write_mode,
        ml_device_used=ml_device_used,
        training_mode=training_mode,
    )

    send_update_success_notification(
        forecast_name=result.forecast_name,
        source=source,
        records_written=records_written,
        day_ahead_points=len(day_ahead_values),
        ml_device_used=ml_device_used,
        training_mode=training_mode,
        ml_compare_mae=ml_compare_mae,
        ml_compare_p95_abs=ml_compare_p95_abs,
        ml_compare_max_abs=ml_compare_max_abs,
        ml_error=ml_error,
    )

    send_daily_digest_notification(
        forecast_name=result.forecast_name,
        source=source,
        records_written=records_written,
        ml_device_used=ml_device_used,
        day_ahead_values=tuple(day_ahead_values),
    )

    if gpu_requested and not gpu_active:
        send_gpu_alert_notification(
            reason=gpu_probe.reason or "GPU compatibility probe failed.",
            gpu_name=gpu_probe.gpu_name,
        )

    staleness_reasons: list[str] = []
    if pipeline.source != "nordpool":
        staleness_reasons.append(f"ingest source={pipeline.source}")
    if pipeline.ingest_error:
        staleness_reasons.append(f"ingest_error={pipeline.ingest_error}")
    if pipeline.retries_used > 0:
        staleness_reasons.append(f"retries_used={pipeline.retries_used}")
    if pipeline.raw_points < len(day_ahead_values):
        staleness_reasons.append(f"raw_points={pipeline.raw_points} aligned_points={len(day_ahead_values)}")

    if staleness_reasons:
        signature = "|".join(staleness_reasons)
        send_pipeline_staleness_alert_notification(
            summary="; ".join(staleness_reasons),
            signature=signature,
        )
    else:
        clear_pipeline_staleness_alert_state()

    parity_threshold_hit = (
        (ml_compare_mae is not None and ml_compare_mae > PARITY_ALERT_THRESHOLDS["mae"])
        or (ml_compare_p95_abs is not None and ml_compare_p95_abs > PARITY_ALERT_THRESHOLDS["p95_abs"])
        or (ml_compare_max_abs is not None and ml_compare_max_abs > PARITY_ALERT_THRESHOLDS["max_abs"])
    )
    if parity_threshold_hit:
        send_parity_alert_notification(
            forecast_name=result.forecast_name,
            mae=ml_compare_mae,
            p95_abs=ml_compare_p95_abs,
            max_abs=ml_compare_max_abs,
        )

    return output
