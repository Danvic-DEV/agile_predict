export interface ForecastSummary {
  id: number;
  name: string;
  created_at: string;
}

export interface AgilePricePoint {
  date_time: string;
  agile_pred: number;
  agile_low?: number | null;
  agile_high?: number | null;
  region?: string | null;
}

export interface ForecastWithPrices {
  id: number;
  name: string;
  created_at: string;
  prices: AgilePricePoint[];
}

export interface LatestForecastDiagnostics {
  forecast_id: number;
  forecast_name: string;
  created_at: string;
  agile_points_total: number;
  agile_points_region_g: number;
  forecast_data_count: number;
  forecast_data_first_date_time: string | null;
  forecast_data_last_date_time: string | null;
  day_ahead_mean: number | null;
  demand_mean: number | null;
  update_source: string | null;
  update_forecast_name: string | null;
  update_records_written: number | null;
  update_day_ahead_points: number | null;
  update_source_updated_at: string | null;
  update_ingest_error: string | null;
  update_raw_points: number | null;
  update_aligned_points: number | null;
  update_interpolated_points: number | null;
  update_retries_used: number | null;
  update_ml_error: string | null;
  update_ml_training_rows: number | null;
  update_ml_test_rows: number | null;
  update_ml_cv_mean_rmse: number | null;
  update_ml_cv_stdev_rmse: number | null;
  update_ml_feature_version: string | null;
  update_ml_range_mode: string | null;
  update_ml_candidate_points: number | null;
  update_ml_compare_mae: number | null;
  update_ml_compare_max_abs: number | null;
  update_ml_compare_p95_abs: number | null;
  update_ml_write_mode: string | null;
  update_ml_device_used: string | null;
  training_mode: boolean;
}

export interface BootstrapForecastBundleRequest {
  points: number;
  idempotency_key: string;
  replace_existing: boolean;
  regions: string[];
  write_agile_data: boolean;
}

export interface BootstrapForecastBundleResponse {
  forecast_name: string;
  forecast_id: number;
  forecast_data_points_written: number;
  agile_data_points_written: number;
  regions: string[];
  created_at: string;
  idempotent_hit: boolean;
}

export interface RunUpdateJobResponse {
  forecast_name: string;
  records_written: number;
  source?: string | null;
  day_ahead_points?: number | null;
}

export interface LatestParitySummary {
  report_available: boolean;
  all_passed: boolean | null;
  failure_count: number | null;
  failures: string[];
  endpoint_count: number | null;
  data_stats_check_count: number | null;
  min_common_points: number | null;
  worst_mean_abs_diff: number | null;
  worst_max_abs_diff: number | null;
  worst_p95_abs_diff: number | null;
  thresholds: Record<string, number> | null;
  report_updated_at: string | null;
  report_path: string | null;
  report_sha256: string | null;
}

export interface ParityHistoryItem {
  report_available: boolean;
  all_passed: boolean | null;
  failure_count: number | null;
  report_updated_at: string | null;
  report_path: string | null;
  report_sha256: string | null;
}

export interface ParityHistoryResponse {
  items: ParityHistoryItem[];
  total: number;
  limit: number;
  offset: number;
  returned: number;
}

export interface MlParityScorecard {
  report_available: boolean;
  training_mode: boolean;
  configured_write_mode: string | null;
  effective_mode: string;
  sample_size: number;
  window_size: number;
  rolling_mae_vs_deterministic: number | null;
  rolling_p95_abs_vs_deterministic: number | null;
  rolling_max_abs_vs_deterministic: number | null;
  confidence_percent: number;
  confidence_label: string;
  latest_error: string | null;
}

export interface MlGpuStatus {
  enabled: boolean;
  tested: boolean;
  compatible: boolean;
  active: boolean;
  gpu_name: string | null;
  reason: string | null;
  xgboost_version: string | null;
  tested_at: string | null;
}

export interface MlWriteModeStatus {
  mode: "deterministic" | "shadow" | "ml";
}

export interface DiscordNotificationPreferences {
  update_started: boolean;
  update_success: boolean;
  update_failure: boolean;
  parity_alert: boolean;
  gpu_alert: boolean;
  daily_digest: boolean;
  pipeline_staleness: boolean;
}

export interface DiscordConfigStatus {
  enabled: boolean;
  webhook_url: string | null;
  notifications: DiscordNotificationPreferences;
}

export interface DiscordConfigRequest {
  webhook_url: string | null;
  notifications: DiscordNotificationPreferences;
}

export interface DiscordTestResponse {
  sent: boolean;
  detail: string;
}

export interface PipelineStageStatus {
  key: string;
  label: string;
  status: string;
  current: number;
  target: number;
  detail: string;
}

export interface SourceCollectionStatus {
  key: string;
  label: string;
  status: string;
  total_rows: number;
  rows_24h: number;
  last_seen: string | null;
  recent_min: number | null;
  recent_max: number | null;
}

export interface IngestPipelineHealth {
  generated_at: string;
  training_mode: boolean;
  next_action: string;
  all_sources_healthy: boolean;
  healthy_source_count: number;
  expected_source_count: number;
  stages: PipelineStageStatus[];
  sources: SourceCollectionStatus[];
}

export interface PipelineTruthIssue {
  code: string;
  severity: string;
  detail: string;
}

export interface PipelineTruthAudit {
  generated_at: string;
  trust_level: string;
  latest_forecast_id: number | null;
  latest_forecast_created_at: string | null;
  latest_forecast_rows: number;
  latest_unique_slots: number;
  latest_duplicate_slots: number;
  latest_day_ahead_non_null_rows: number;
  latest_day_ahead_zero_rows: number;
  latest_day_ahead_zero_ratio: number | null;
  latest_data_last_seen: string | null;
  latest_data_freshness_minutes: number | null;
  issues: PipelineTruthIssue[];
}
