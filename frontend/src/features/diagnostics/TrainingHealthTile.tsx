import { useEffect, useState } from "react";
import { fetchTrainingDataHealth } from "./api";
import type { TrainingDataHealthResponse } from "../../lib/api/types";

interface TrainingHealthTileProps {
  region?: string;
}

function healthStatusClass(status: string): "ok" | "warn" | "error" {
  if (status === "healthy") return "ok";
  if (status === "degraded") return "warn";
  return "error";
}

function severityIcon(severity: string): string {
  if (severity === "critical") return "🔴";
  if (severity === "warning") return "⚠️";
  return "ℹ️";
}

export function TrainingHealthTile({ region = "B" }: TrainingHealthTileProps) {
  const [data, setData] = useState<TrainingDataHealthResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;

    async function load() {
      try {
        const result = await fetchTrainingDataHealth(region);
        if (mounted) {
          setData(result);
          setError(null);
        }
      } catch (err) {
        if (mounted) {
          setError(err instanceof Error ? err.message : "Failed to load training health");
        }
      } finally {
        if (mounted) {
          setLoading(false);
        }
      }
    }

    load();

    return () => {
      mounted = false;
    };
  }, [region]);

  if (loading) {
    return (
      <div className="progressive-card is-loading">
        <h3>Training Data Health</h3>
        <div className="skeleton-stack">
          <div className="skeleton-line long" />
          <div className="skeleton-line" />
          <div className="skeleton-line short" />
        </div>
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="progressive-card is-ready">
        <h3>Training Data Health</h3>
        <p className="error-text">{error || "No data available"}</p>
      </div>
    );
  }

  const healthClass = healthStatusClass(data.health_status);
  const breakdown = data.data_source_breakdown;
  const coverage = data.weather_coverage;

  return (
    <div className="progressive-card is-ready">
      <div className="chart-header">
        <h3>Training Data Health (Region {region})</h3>
        <span className={`section-health-pill ${healthClass}`}>
          {data.health_status.toUpperCase()}
        </span>
      </div>
      
      <p className="section-meta">Updated: {new Date(data.generated_at).toLocaleString()}</p>

      {/* Data Source Breakdown */}
      <div className="metric-grid" style={{ marginTop: 16 }}>
        <div className="metric-item">
          <span className="label">Agile Actual Prices</span>
          <strong>{breakdown.agile_actual_count.toLocaleString()}</strong>
          <span className="detail">{breakdown.agile_percent.toFixed(1)}% of total</span>
        </div>
        <div className="metric-item">
          <span className="label">Nordpool Fallback</span>
          <strong>{breakdown.nordpool_count.toLocaleString()}</strong>
          <span className="detail">{breakdown.nordpool_percent.toFixed(1)}% of total</span>
        </div>
        <div className="metric-item">
          <span className="label">Training Points</span>
          <strong>{coverage.estimated_training_points.toLocaleString()}</strong>
          <span className="detail">Estimated after weather join</span>
        </div>
        <div className="metric-item">
          <span className="label">Backfill Coverage</span>
          <strong>{coverage.backfill_forecast_count} forecasts</strong>
          <span className="detail">{coverage.backfill_date_range || "No backfill data"}</span>
        </div>
      </div>

      {/* Alerts */}
      {data.alerts && data.alerts.length > 0 && (
        <div style={{ marginTop: 16 }}>
          <h4 style={{ marginBottom: 8, fontSize: "14px", fontWeight: 600 }}>Alerts</h4>
          {data.alerts.map((alert, index) => (
            <div
              key={index}
              className={`alert-box alert-${alert.severity}`}
              style={{ marginBottom: 8, padding: 12, borderRadius: 4, border: "1px solid rgba(255,255,255,0.1)" }}
            >
              <div style={{ display: "flex", alignItems: "flex-start", gap: 8 }}>
                <span style={{ fontSize: "16px" }}>{severityIcon(alert.severity)}</span>
                <div style={{ flex: 1 }}>
                  <div style={{ fontWeight: 600, marginBottom: 4 }}>{alert.title}</div>
                  <div style={{ fontSize: "13px", marginBottom: 4 }}>{alert.message}</div>
                  <div style={{ fontSize: "12px", opacity: 0.8, marginBottom: 4 }}>
                    <strong>Impact:</strong> {alert.impact}
                  </div>
                  {alert.fix_action && (
                    <div style={{ fontSize: "12px", opacity: 0.8 }}>
                      <strong>Fix:</strong> {alert.fix_action}
                      {alert.fix_endpoint && (
                        <code style={{ marginLeft: 4, padding: "2px 4px", background: "rgba(0,0,0,0.2)", borderRadius: 2 }}>
                          {alert.fix_endpoint}
                        </code>
                      )}
                    </div>
                  )}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Recommendations */}
      {data.recommendations && data.recommendations.length > 0 && (
        <div style={{ marginTop: 16 }}>
          <h4 style={{ marginBottom: 8, fontSize: "14px", fontWeight: 600 }}>Recommendations</h4>
          <ul style={{ margin: 0, paddingLeft: 20, fontSize: "13px", lineHeight: 1.6 }}>
            {data.recommendations.map((rec, index) => (
              <li key={index}>{rec}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
