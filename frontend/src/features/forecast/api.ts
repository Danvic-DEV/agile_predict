import { getJson } from "../../lib/api/client";
import type { ForecastSummary, ForecastWithPrices, LatestForecastDiagnostics } from "../../lib/api/types";

export function fetchForecasts(limit = 5): Promise<ForecastSummary[]> {
  return getJson<ForecastSummary[]>(`/forecasts?limit=${limit}`);
}

export function fetchDiagnosticsSummary(): Promise<LatestForecastDiagnostics> {
  return getJson<LatestForecastDiagnostics>("/diagnostics/latest-summary");
}

export function fetchForecastPrices(region = "G", days = 7, forecastCount = 1): Promise<ForecastWithPrices[]> {
  const params = new URLSearchParams();
  if (region) {
    params.set("region", region);
  }
  params.set("days", String(days));
  params.set("forecast_count", String(forecastCount));
  params.set("high_low", "true");
  return getJson<ForecastWithPrices[]>(`/forecasts/prices?${params.toString()}`);
}

export function fetchRegions(): Promise<string[]> {
  return getJson<string[]>("/forecasts/regions");
}
