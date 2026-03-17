from src.core.settings import settings
from src.core.update_job_state import write_last_update_job_state
from src.domain.bootstrap_bundle import BootstrapBundleConfig, write_bootstrap_bundle
from src.domain.forecast_pipeline import ForecastRunResult, run_forecast_pipeline
from src.ml.parity.day_ahead_xgb import check_ml_training_readiness, run_ml_day_ahead_forecast
from src.repositories.unit_of_work import UnitOfWork


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

    # Auto-enable real ML only when strict readiness checks pass.
    configured_ml_write_mode = settings.ml_write_mode
    ml_ready, ml_ready_reason = check_ml_training_readiness(uow=uow)
    auto_enable_ml = configured_ml_write_mode == "deterministic" and ml_ready
    ml_write_mode = "ml" if auto_enable_ml else configured_ml_write_mode
    training_mode = ml_write_mode == "deterministic"
    if training_mode and ml_ready_reason is not None:
        ml_error = ml_ready_reason

    if ml_write_mode in {"ml", "shadow"}:
        try:
            ml_output = run_ml_day_ahead_forecast(
                uow=uow,
                point_count=len(day_ahead_values),
                bridge_day_ahead_values=deterministic_values,
            )
        except Exception as exc:
            ml_error = str(exc)
            if auto_enable_ml:
                # Stay in training mode until ML can run successfully.
                ml_write_mode = "deterministic"
                training_mode = True
                day_ahead_values = deterministic_values
                day_ahead_low_values = None
                day_ahead_high_values = None
                source = pipeline.source
            elif not settings.allow_ml_fallback:
                raise RuntimeError(f"ML forecast failed with fallback disabled: {exc}") from exc
            else:
                day_ahead_values = deterministic_values
                day_ahead_low_values = None
                day_ahead_high_values = None
                source = pipeline.source
        else:
            ml_candidate_points = len(ml_output.day_ahead_values)

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
        day_ahead_values = deterministic_values
        day_ahead_low_values = None
        day_ahead_high_values = None
        source = pipeline.source

    result = write_bootstrap_bundle(
        uow=uow,
        config=BootstrapBundleConfig(
            points=len(day_ahead_values),
            idempotency_key="update-job-seed",
            replace_existing=True,
            regions=tuple(settings.bootstrap_regions_list),
            day_ahead_values=day_ahead_values,
            day_ahead_low_values=day_ahead_low_values,
            day_ahead_high_values=day_ahead_high_values,
            forecast_mean=ml_cv_mean_rmse,
            forecast_stdev=ml_cv_stdev_rmse,
            write_agile_data=True,
        ),
    )

    records_written = result.forecast_data_points_written + result.agile_data_points_written
    output = ForecastRunResult(
        records_written=records_written,
        forecast_name=result.forecast_name,
        source=source,
        day_ahead_points=len(day_ahead_values),
    )

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
        training_mode=training_mode,
    )

    return output
