from datetime import datetime

from pydantic import BaseModel


class LatestForecastDiagnostics(BaseModel):
    forecast_id: int
    forecast_name: str
    created_at: datetime
    agile_points_total: int
    agile_points_region_g: int
    forecast_data_count: int
    forecast_data_first_date_time: str | None
    forecast_data_last_date_time: str | None
    day_ahead_mean: float | None
    demand_mean: float | None
    update_source: str | None
    update_forecast_name: str | None
    update_records_written: int | None
    update_day_ahead_points: int | None
    update_source_updated_at: str | None
    update_ingest_error: str | None
    update_raw_points: int | None
    update_aligned_points: int | None
    update_interpolated_points: int | None
    update_retries_used: int | None


class LatestParitySummary(BaseModel):
    report_available: bool
    all_passed: bool | None
    failure_count: int | None
    failures: list[str]
    endpoint_count: int | None
    data_stats_check_count: int | None
    min_common_points: int | None
    worst_mean_abs_diff: float | None
    worst_max_abs_diff: float | None
    worst_p95_abs_diff: float | None
    thresholds: dict[str, float | int] | None
    report_updated_at: str | None
    report_path: str | None
    report_sha256: str | None


class ParityHistoryItem(BaseModel):
    report_available: bool
    all_passed: bool | None
    failure_count: int | None
    report_updated_at: str | None
    report_path: str | None
    report_sha256: str | None


class ParityHistoryResponse(BaseModel):
    items: list[ParityHistoryItem]
    total: int
    limit: int
    offset: int
    returned: int
