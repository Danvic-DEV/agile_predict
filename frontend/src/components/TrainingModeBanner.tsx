import { useEffect, useState } from "react";
import { fetchLatestDiagnostics, fetchMlParityScorecard } from "../features/diagnostics/api";
import type { LatestForecastDiagnostics, MlParityScorecard } from "../lib/api/types";

export function TrainingModeBanner() {
  const [diagnostics, setDiagnostics] = useState<LatestForecastDiagnostics | null>(
    null
  );
  const [scorecard, setScorecard] = useState<MlParityScorecard | null>(null);
  const [refreshError, setRefreshError] = useState("");

  useEffect(() => {
    async function fetch() {
      try {
        const [diagnosticsResult, scorecardResult] = await Promise.all([
          fetchLatestDiagnostics(),
          fetchMlParityScorecard(30),
        ]);
        setDiagnostics(diagnosticsResult);
        setScorecard(scorecardResult);
        setRefreshError("");
      } catch (err) {
        setRefreshError(
          err instanceof Error ? err.message : "Failed to fetch diagnostics"
        );
      }
    }

    fetch();
    const interval = setInterval(fetch, 30000); // Refresh every 30 seconds
    return () => clearInterval(interval);
  }, []);

  if (!diagnostics) {
    return null;
  }

  const confidenceLabel = scorecard?.confidence_label ?? "low";
  const confidencePct = scorecard?.confidence_percent ?? 0;
  const shouldShowCollapsedPill =
    !diagnostics.training_mode &&
    scorecard?.effective_mode === "ml" &&
    confidencePct >= 80;

  if (shouldShowCollapsedPill) {
    return (
      <div className="status-pill">
        <span className="status-pill-title">ML Active</span>
        <span className={`scorecard-confidence ${confidenceLabel}`}>
          {confidenceLabel.toUpperCase()} confidence
        </span>
        <span className="banner-meta-value">{confidencePct.toFixed(2)}%</span>
      </div>
    );
  }

  if (!diagnostics.training_mode) {
    return null;
  }

  return (
    <div className="training-mode-banner">
      <div className="banner-content">
        <span className="banner-icon">📊</span>
        <div className="banner-text">
          <strong>Customer Forecasts Disabled</strong>
          <div className="banner-meta-row">
            <span className={`scorecard-confidence ${confidenceLabel}`}>
              {confidenceLabel.toUpperCase()} confidence
            </span>
            <span className="banner-meta-value">{confidencePct.toFixed(2)}%</span>
            <span className="banner-meta-value">
              Samples: {scorecard?.sample_size ?? 0}/{scorecard?.window_size ?? 30}
            </span>
          </div>
          <p>
            The system is still in training or deterministic mode. Customer-facing forecasts remain blocked until a trusted ML forecast is available.
          </p>
          {refreshError && <p className="banner-error">{refreshError}</p>}
        </div>
      </div>
    </div>
  );
}
