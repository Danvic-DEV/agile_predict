import { getJson, postJson } from "../../lib/api/client";
import type {
  BootstrapForecastBundleRequest,
  BootstrapForecastBundleResponse,
  IngestPipelineHealth,
  LatestForecastDiagnostics,
  MlParityScorecard,
  ParityHistoryResponse,
  LatestParitySummary,
  RunUpdateJobResponse,
} from "../../lib/api/types";

export function fetchLatestDiagnostics(): Promise<LatestForecastDiagnostics> {
  return getJson<LatestForecastDiagnostics>("/diagnostics/latest-summary");
}

export function fetchLatestParitySummary(): Promise<LatestParitySummary> {
  return getJson<LatestParitySummary>("/diagnostics/parity-last-summary");
}

export function fetchMlParityScorecard(windowSize = 30): Promise<MlParityScorecard> {
  const params = new URLSearchParams();
  params.set("window_size", String(windowSize));
  return getJson<MlParityScorecard>(`/diagnostics/ml-parity-scorecard?${params.toString()}`);
}

export function fetchIngestPipelineHealth(): Promise<IngestPipelineHealth> {
  return getJson<IngestPipelineHealth>("/diagnostics/ingest-pipeline-health");
}

export function fetchParityHistory(options?: {
  limit?: number;
  offset?: number;
  status?: "pass" | "fail";
  since?: string;
  until?: string;
}): Promise<ParityHistoryResponse> {
  const params = new URLSearchParams();
  params.set("limit", String(options?.limit ?? 5));
  params.set("offset", String(options?.offset ?? 0));
  if (options?.status) {
    params.set("status", options.status);
  }
  if (options?.since) {
    params.set("since", options.since);
  }
  if (options?.until) {
    params.set("until", options.until);
  }

  return getJson<ParityHistoryResponse>(`/diagnostics/parity-history?${params.toString()}`);
}

export function runBootstrapForecastBundle(
  payload: BootstrapForecastBundleRequest
): Promise<BootstrapForecastBundleResponse> {
  return postJson<BootstrapForecastBundleResponse, BootstrapForecastBundleRequest>(
    "/admin-jobs/bootstrap-forecast-bundle",
    payload
  );
}

export function runUpdateForecastJob(): Promise<RunUpdateJobResponse> {
  return postJson<RunUpdateJobResponse, Record<string, never>>("/admin-jobs/run-update-forecast-job", {});
}
