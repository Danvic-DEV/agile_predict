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
