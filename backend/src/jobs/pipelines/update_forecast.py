from src.core.settings import settings
from src.core.update_job_state import write_last_update_job_state
from src.domain.bootstrap_bundle import BootstrapBundleConfig, write_bootstrap_bundle
from src.domain.forecast_pipeline import ForecastRunResult, run_forecast_pipeline
from src.repositories.unit_of_work import UnitOfWork


def run_update_forecast_job(uow: UnitOfWork) -> ForecastRunResult:
    pipeline = run_forecast_pipeline(fallback_points=settings.auto_bootstrap_points)
    day_ahead_values = pipeline.day_ahead_values
    source = pipeline.source

    result = write_bootstrap_bundle(
        uow=uow,
        config=BootstrapBundleConfig(
            points=len(day_ahead_values),
            idempotency_key="update-job-seed",
            replace_existing=True,
            regions=tuple(settings.bootstrap_regions_list),
            day_ahead_values=day_ahead_values,
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
    )

    return output
