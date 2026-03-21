import { getJson, postJson } from "../../lib/api/client";
import type {
  BootstrapForecastBundleRequest,
  BootstrapForecastBundleResponse,
  DiscordConfigRequest,
  DiscordConfigStatus,
  DiscordTestResponse,
  IngestPipelineHealth,
  LatestForecastDiagnostics,
  MlGpuStatus,
  MlParityScorecard,
  PipelineTruthAudit,
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

export function fetchMlGpuStatus(): Promise<MlGpuStatus> {
  return getJson<MlGpuStatus>("/diagnostics/ml-gpu-status");
}

export function setMlGpuStatus(enabled: boolean): Promise<MlGpuStatus> {
  return postJson<MlGpuStatus, { enabled: boolean }>("/diagnostics/ml-gpu-status", { enabled });
}

export function fetchDiscordConfig(): Promise<DiscordConfigStatus> {
  return getJson<DiscordConfigStatus>("/diagnostics/discord-config");
}

export function setDiscordConfig(payload: DiscordConfigRequest): Promise<DiscordConfigStatus> {
  return postJson<DiscordConfigStatus, DiscordConfigRequest>("/diagnostics/discord-config", payload);
}

export function sendDiscordTest(): Promise<DiscordTestResponse> {
  return postJson<DiscordTestResponse, Record<string, never>>("/diagnostics/discord-test", {});
}

export function fetchFeedHealth(): Promise<Record<string, unknown>> {
  return getJson<Record<string, unknown>>("/diagnostics/feed-health");
}

export function fetchIngestPipelineHealth(): Promise<IngestPipelineHealth> {
  return getJson<IngestPipelineHealth>("/diagnostics/ingest-pipeline-health");
}

export function fetchPipelineTruthAudit(): Promise<PipelineTruthAudit> {
  return getJson<PipelineTruthAudit>("/diagnostics/pipeline-truth-audit");
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
