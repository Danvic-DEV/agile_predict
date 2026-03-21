import { useEffect, useMemo, useState } from "react";

import { fetchForecastPrices, fetchForecasts, fetchRegions } from "./api";
import { ApiError } from "../../lib/api/client";
import type { AgilePricePoint, ForecastSummary, ForecastWithPrices } from "../../lib/api/types";

type LoadState = "idle" | "loading" | "loaded" | "error";

type CustomerForecastStatus = "available" | "disabled";

const CHART_WIDTH = 720;
const CHART_HEIGHT = 240;
const CHART_PADDING = 24;

function buildChartPath(points: AgilePricePoint[]): string {
  if (points.length === 0) {
    return "";
  }

  const values = points.map((point) => point.agile_pred);
  const minValue = Math.min(...values);
  const maxValue = Math.max(...values);
  const valueRange = Math.max(maxValue - minValue, 0.0001);
  const width = CHART_WIDTH - CHART_PADDING * 2;
  const height = CHART_HEIGHT - CHART_PADDING * 2;

  return points
    .map((point, index) => {
      const x = CHART_PADDING + (index / Math.max(points.length - 1, 1)) * width;
      const y = CHART_HEIGHT - CHART_PADDING - ((point.agile_pred - minValue) / valueRange) * height;
      return `${index === 0 ? "M" : "L"}${x.toFixed(2)},${y.toFixed(2)}`;
    })
    .join(" ");
}

function formatSlotLabel(dateTime: string): string {
  return new Intl.DateTimeFormat("en-GB", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    timeZone: "Europe/London",
  }).format(new Date(dateTime));
}

export function ForecastDashboard() {
  const [forecasts, setForecasts] = useState<ForecastSummary[]>([]);
  const [prices, setPrices] = useState<ForecastWithPrices[]>([]);
  const [regions, setRegions] = useState<string[]>(["ALL"]);
  const [selectedRegion, setSelectedRegion] = useState("ALL");
  const [days, setDays] = useState(7);
  const [forecastCount, setForecastCount] = useState(1);
  const [state, setState] = useState<LoadState>("idle");
  const [error, setError] = useState<string>("");
  const [customerForecastStatus, setCustomerForecastStatus] = useState<CustomerForecastStatus>("available");
  const [refreshToken, setRefreshToken] = useState(0);

  useEffect(() => {
    let active = true;

    async function load() {
      setState("loading");
      try {
        const [f, p, r] = await Promise.all([
          fetchForecasts(5),
          fetchForecastPrices(selectedRegion === "ALL" ? "" : selectedRegion, days, forecastCount),
          fetchRegions(),
        ]);
        if (!active) {
          return;
        }
        setForecasts(f);
        setPrices(p);
        const options = ["ALL", ...r];
        setRegions(options);
        if (!options.includes(selectedRegion)) {
          setSelectedRegion("ALL");
        }
        setCustomerForecastStatus("available");
        setState("loaded");
        setError("");
      } catch (err) {
        if (!active) {
          return;
        }
        if (err instanceof ApiError && err.code === "customer_forecast_unavailable") {
          setCustomerForecastStatus("disabled");
          setError(err.message);
        } else {
          setCustomerForecastStatus("available");
          setError(err instanceof Error ? err.message : "Failed loading forecast dashboard");
        }
        setState("error");
      }
    }

    load();
    return () => {
      active = false;
    };
  }, [days, forecastCount, selectedRegion, refreshToken]);

  const latest = useMemo(() => prices[0], [prices]);
  const latestSummary = useMemo(() => {
    if (!latest || latest.prices.length === 0) {
      return null;
    }

    const values = latest.prices.map((point) => point.agile_pred);
    const total = values.reduce((sum, value) => sum + value, 0);
    return {
      min: Math.min(...values),
      max: Math.max(...values),
      avg: total / values.length,
      firstSlot: latest.prices[0],
      lastSlot: latest.prices[latest.prices.length - 1],
      chartPath: buildChartPath(latest.prices),
      recentSlots: latest.prices.slice(0, 10),
    };
  }, [latest]);

  return (
    <section className="card">
      <h2>Forecast Dashboard</h2>
      <div className="controls-row">
        <label>
          Region
          <select value={selectedRegion} onChange={(e) => setSelectedRegion(e.target.value)}>
            {regions.map((region) => (
              <option key={region} value={region}>
                {region}
              </option>
            ))}
          </select>
        </label>
        <label>
          Days
          <input
            type="number"
            min={1}
            max={14}
            value={days}
            onChange={(e) => setDays(Number(e.target.value) || 1)}
          />
        </label>
        <label>
          Forecast Count
          <input
            type="number"
            min={1}
            max={5}
            value={forecastCount}
            onChange={(e) => setForecastCount(Number(e.target.value) || 1)}
          />
        </label>
        <button type="button" onClick={() => setRefreshToken((v) => v + 1)}>
          Refresh
        </button>
      </div>
      {state === "loading" && <p>Loading latest forecast snapshots...</p>}
      {state === "error" && customerForecastStatus === "disabled" && (
        <div className="forecast-blocked-card" role="alert">
          <strong>Customer forecast output is disabled</strong>
          <p>{error}</p>
          <p>
            The current pipeline is not serving trusted ML output, so customer-facing forecast data is intentionally blocked.
          </p>
        </div>
      )}
      {state === "error" && customerForecastStatus === "available" && <p>Load failed: {error}</p>}
      {state === "loaded" && (
        <>
          <div className="metric-grid">
            <div>
              <span className="label">Tracked Forecasts</span>
              <strong>{forecasts.length}</strong>
            </div>
            <div>
              <span className="label">Latest Forecast Rows</span>
              <strong>{latest?.prices.length ?? 0}</strong>
            </div>
            <div>
              <span className="label">Latest Forecast</span>
              <strong>{latest?.name ?? "n/a"}</strong>
            </div>
            <div>
              <span className="label">Created</span>
              <strong>{latest ? formatSlotLabel(latest.created_at) : "n/a"}</strong>
            </div>
          </div>
          {latestSummary && (
            <>
              <div className="metric-grid" style={{ marginTop: 12 }}>
                <div>
                  <span className="label">First Slot</span>
                  <strong>{formatSlotLabel(latestSummary.firstSlot.date_time)}</strong>
                </div>
                <div>
                  <span className="label">Last Slot</span>
                  <strong>{formatSlotLabel(latestSummary.lastSlot.date_time)}</strong>
                </div>
                <div>
                  <span className="label">Min Agile Pred</span>
                  <strong>{latestSummary.min.toFixed(2)} p/kWh</strong>
                </div>
                <div>
                  <span className="label">Max Agile Pred</span>
                  <strong>{latestSummary.max.toFixed(2)} p/kWh</strong>
                </div>
                <div>
                  <span className="label">Avg Agile Pred</span>
                  <strong>{latestSummary.avg.toFixed(2)} p/kWh</strong>
                </div>
                <div>
                  <span className="label">Region</span>
                  <strong>{selectedRegion === "ALL" ? "All Regions" : selectedRegion}</strong>
                </div>
              </div>
              <div className="chart-card">
                <div className="chart-header">
                  <h3>Latest Agile Prediction Curve</h3>
                  <span>{latest.prices.length} half-hour slots</span>
                </div>
                <svg viewBox={`0 0 ${CHART_WIDTH} ${CHART_HEIGHT}`} className="forecast-chart" role="img" aria-label="Latest agile price forecast chart">
                  <path d={latestSummary.chartPath} className="forecast-chart-line" />
                </svg>
              </div>
              <div className="table-card">
                <div className="chart-header">
                  <h3>Upcoming Slots</h3>
                  <span>First 10 rows</span>
                </div>
                <table className="slot-table">
                  <thead>
                    <tr>
                      <th>Slot</th>
                      <th>Pred</th>
                      <th>Low</th>
                      <th>High</th>
                    </tr>
                  </thead>
                  <tbody>
                    {latestSummary.recentSlots.map((slot) => (
                      <tr key={slot.date_time}>
                        <td>{formatSlotLabel(slot.date_time)}</td>
                        <td>{slot.agile_pred.toFixed(2)}</td>
                        <td>{slot.agile_low?.toFixed(2) ?? "n/a"}</td>
                        <td>{slot.agile_high?.toFixed(2) ?? "n/a"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </>
      )}
    </section>
  );
}
