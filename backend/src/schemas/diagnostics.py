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
    update_ml_error: str | None
    update_ml_training_rows: int | None
    update_ml_test_rows: int | None
    update_ml_cv_mean_rmse: float | None
    update_ml_cv_stdev_rmse: float | None
    update_ml_feature_version: str | None
    update_ml_range_mode: str | None
    update_ml_candidate_points: int | None
    update_ml_compare_mae: float | None
    update_ml_compare_max_abs: float | None
    update_ml_compare_p95_abs: float | None
    update_ml_write_mode: str | None
    training_mode: bool = False


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


class MlParityScorecard(BaseModel):
    report_available: bool
    training_mode: bool
    configured_write_mode: str | None
    effective_mode: str
    sample_size: int
    window_size: int
    rolling_mae_vs_deterministic: float | None
    rolling_p95_abs_vs_deterministic: float | None
    rolling_max_abs_vs_deterministic: float | None
    confidence_percent: float
    confidence_label: str
    latest_error: str | None


class PipelineStageStatus(BaseModel):
    key: str
    label: str
    status: str
    current: int
    target: int
    detail: str


class SourceCollectionStatus(BaseModel):
    key: str
    label: str
    status: str
    total_rows: int
    rows_24h: int
    last_seen: str | None
    recent_min: float | None
    recent_max: float | None


class IngestPipelineHealth(BaseModel):
    generated_at: str
    training_mode: bool
    next_action: str
    all_sources_healthy: bool
    healthy_source_count: int
    expected_source_count: int
    stages: list[PipelineStageStatus]
    sources: list[SourceCollectionStatus]


class ExternalSystemContextHealth(BaseModel):
    generated_at: str
    total_rows: int
    rows_24h: int
    latest_date_time: str | None
    carbon_intensity_rows: int
    fuel_mix_rows: int
    interconnector_rows: int
    pumped_storage_rows: int
