import { useEffect, useState } from "react";

import {
  fetchLatestDiagnostics,
  fetchParityHistory,
  fetchLatestParitySummary,
  runBootstrapForecastBundle,
  runUpdateForecastJob,
} from "./api";
import type { LatestForecastDiagnostics, LatestParitySummary, ParityHistoryItem } from "../../lib/api/types";

const HISTORY_PAGE_SIZE = 5;

function formatUpdateRelativeTime(value: string | null, nowMs: number): string {
  if (!value) {
    return "n/a";
  }

  const timestamp = Date.parse(value);
  if (Number.isNaN(timestamp)) {
    return "n/a";
  }

  const seconds = Math.max(0, Math.floor((nowMs - timestamp) / 1000));
  if (seconds < 60) {
    return `${seconds}s ago`;
  }

  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) {
    return `${minutes}m ago`;
  }

  const hours = Math.floor(minutes / 60);
  if (hours < 24) {
    return `${hours}h ago`;
  }

  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

function formatUpdateAbsoluteTime(value: string | null): string {
  if (!value) {
    return "n/a";
  }

  const timestamp = Date.parse(value);
  if (Number.isNaN(timestamp)) {
    return "n/a";
  }

  return new Intl.DateTimeFormat("en-GB", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
    timeZone: "UTC",
  }).format(new Date(timestamp));
}

export function DiagnosticsPanel() {
  const [nowMs, setNowMs] = useState(() => Date.now());
  const [data, setData] = useState<LatestForecastDiagnostics | null>(null);
  const [parity, setParity] = useState<LatestParitySummary | null>(null);
  const [parityHistory, setParityHistory] = useState<ParityHistoryItem[]>([]);
  const [error, setError] = useState("");
  const [parityError, setParityError] = useState("");
  const [actionState, setActionState] = useState<"idle" | "running">("idle");
  const [actionMessage, setActionMessage] = useState("");
  const [seedPoints, setSeedPoints] = useState(48);
  const [seedRegionsInput, setSeedRegionsInput] = useState("X,G");
  const [seedReplaceExisting, setSeedReplaceExisting] = useState(true);
  const [seedWriteAgileData, setSeedWriteAgileData] = useState(true);
  const [historyStatusFilter, setHistoryStatusFilter] = useState<"all" | "pass" | "fail">("all");
  const [historyWindowHours, setHistoryWindowHours] = useState(168);
  const [historyOffset, setHistoryOffset] = useState(0);
  const [historyTotal, setHistoryTotal] = useState(0);

  const parityStatus = parity?.report_available
    ? parity.all_passed
      ? "PASS"
      : "FAIL"
    : "Not Available";

  function buildHistorySinceIso(): string | undefined {
    if (!Number.isFinite(historyWindowHours) || historyWindowHours <= 0) {
      return undefined;
    }
    const now = Date.now();
    const sinceMs = now - historyWindowHours * 60 * 60 * 1000;
    return new Date(sinceMs).toISOString();
  }

  async function refreshDiagnostics() {
    const result = await fetchLatestDiagnostics();
    setData(result);
    setError("");
  }

  async function refreshParity() {
    try {
      const [result, history] = await Promise.all([
        fetchLatestParitySummary(),
        fetchParityHistory({
          limit: HISTORY_PAGE_SIZE,
          offset: historyOffset,
          status: historyStatusFilter === "all" ? undefined : historyStatusFilter,
          since: buildHistorySinceIso(),
        }),
      ]);
      setParity(result);
      setParityHistory(history.items);
      setHistoryTotal(history.total);
      setParityError("");
    } catch (err) {
      setParityError(err instanceof Error ? err.message : "Failed loading parity summary");
    }
  }

  async function refreshAll() {
    await Promise.all([refreshDiagnostics(), refreshParity()]);
  }

  useEffect(() => {
    let active = true;

    async function load() {
      try {
        const result = await fetchLatestDiagnostics();
        if (!active) {
          return;
        }
        setData(result);
        setError("");

        try {
          const [parityResult, parityHistoryResult] = await Promise.all([
            fetchLatestParitySummary(),
            fetchParityHistory({
              limit: HISTORY_PAGE_SIZE,
              offset: historyOffset,
              status: historyStatusFilter === "all" ? undefined : historyStatusFilter,
              since: buildHistorySinceIso(),
            }),
          ]);
          if (!active) {
            return;
          }
          setParity(parityResult);
          setParityHistory(parityHistoryResult.items);
          setHistoryTotal(parityHistoryResult.total);
          setParityError("");
        } catch (parityErr) {
          if (!active) {
            return;
          }
          setParityError(parityErr instanceof Error ? parityErr.message : "Failed loading parity summary");
        }
      } catch (err) {
        if (!active) {
          return;
        }
        setError(err instanceof Error ? err.message : "Failed loading diagnostics");
      }
    }

    load();
    return () => {
      active = false;
    };
  }, [historyStatusFilter, historyWindowHours, historyOffset]);

  useEffect(() => {
    setHistoryOffset(0);
  }, [historyStatusFilter, historyWindowHours]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      setNowMs(Date.now());
    }, 30000);

    return () => {
      window.clearInterval(timer);
    };
  }, []);

  const hasPreviousHistoryPage = historyOffset > 0;
  const hasNextHistoryPage = historyOffset + parityHistory.length < historyTotal;

  async function handleSeedBundle() {
    setActionState("running");
    setActionMessage("");
    try {
      const parsedRegions = seedRegionsInput
        .split(",")
        .map((region) => region.trim())
        .filter(Boolean);
      if (parsedRegions.length === 0) {
        throw new Error("Provide at least one region code");
      }
      const nowKey = `ui-seed-${Date.now()}`;
      const result = await runBootstrapForecastBundle({
        points: seedPoints,
        idempotency_key: nowKey,
        replace_existing: seedReplaceExisting,
        regions: parsedRegions,
        write_agile_data: seedWriteAgileData,
      });
      await refreshAll();
      setActionMessage(
        `Seeded ${result.forecast_data_points_written} forecast rows and ${result.agile_data_points_written} agile rows.`
      );
    } catch (err) {
      setActionMessage(err instanceof Error ? err.message : "Failed seeding bundle");
    } finally {
      setActionState("idle");
    }
  }

  async function handleRunUpdateJob() {
    setActionState("running");
    setActionMessage("");
    try {
      const result = await runUpdateForecastJob();
      await refreshAll();
      const sourceLabel = result.source ?? "unknown";
      const pointsLabel = result.day_ahead_points ?? "n/a";
      setActionMessage(
        `Update job wrote ${result.records_written} records (${sourceLabel} source, ${pointsLabel} day-ahead points).`
      );
    } catch (err) {
      setActionMessage(err instanceof Error ? err.message : "Failed running update job");
    } finally {
      setActionState("idle");
    }
  }

  return (
    <section className="card">
      <h2>Diagnostics</h2>
      <div className="controls-row">
        <label>
          Seed Points
          <input
            type="number"
            min={1}
            step={1}
            value={seedPoints}
            onChange={(event) => setSeedPoints(Number.parseInt(event.target.value, 10) || 1)}
            disabled={actionState === "running"}
          />
        </label>
        <label>
          Regions (comma-separated)
          <input
            type="text"
            value={seedRegionsInput}
            onChange={(event) => setSeedRegionsInput(event.target.value)}
            disabled={actionState === "running"}
          />
        </label>
        <label>
          Replace Existing
          <select
            value={seedReplaceExisting ? "yes" : "no"}
            onChange={(event) => setSeedReplaceExisting(event.target.value === "yes")}
            disabled={actionState === "running"}
          >
            <option value="yes">Yes</option>
            <option value="no">No</option>
          </select>
        </label>
        <label>
          Write Agile Data
          <select
            value={seedWriteAgileData ? "yes" : "no"}
            onChange={(event) => setSeedWriteAgileData(event.target.value === "yes")}
            disabled={actionState === "running"}
          >
            <option value="yes">Yes</option>
            <option value="no">No</option>
          </select>
        </label>
      </div>
      <div className="controls-row">
        <label>
          History Status
          <select
            value={historyStatusFilter}
            onChange={(event) => setHistoryStatusFilter(event.target.value as "all" | "pass" | "fail")}
            disabled={actionState === "running"}
          >
            <option value="all">All</option>
            <option value="pass">Pass</option>
            <option value="fail">Fail</option>
          </select>
        </label>
        <label>
          History Window (Hours)
          <input
            type="number"
            min={1}
            step={1}
            value={historyWindowHours}
            onChange={(event) => setHistoryWindowHours(Number.parseInt(event.target.value, 10) || 1)}
            disabled={actionState === "running"}
          />
        </label>
      </div>
      <div className="controls-row">
        <button type="button" onClick={handleSeedBundle} disabled={actionState === "running"}>
          Seed Bundle
        </button>
        <button type="button" onClick={handleRunUpdateJob} disabled={actionState === "running"}>
          Run Update Job
        </button>
        <button type="button" onClick={() => void refreshAll()} disabled={actionState === "running"}>
          Refresh Diagnostics
        </button>
      </div>
      {actionMessage && <p>{actionMessage}</p>}
      {error && <p>Diagnostics unavailable: {error}</p>}
      {parityError && <p>Parity summary unavailable: {parityError}</p>}
      {!error && !data && <p>Loading diagnostics...</p>}
      {data && (
        <>
          <div className="update-run-card">
            <h3>Last Update Run</h3>
            <div className="update-run-topline">{formatUpdateRelativeTime(data.update_source_updated_at, nowMs)}</div>
            <div className="update-run-grid">
              <span>Source</span>
              <strong>{data.update_source ?? "n/a"}</strong>
              <span>Records</span>
              <strong>{data.update_records_written ?? "n/a"}</strong>
              <span>Day-Ahead Points</span>
              <strong>{data.update_day_ahead_points ?? "n/a"}</strong>
              <span>Raw Points</span>
              <strong>{data.update_raw_points ?? "n/a"}</strong>
              <span>Aligned Points</span>
              <strong>{data.update_aligned_points ?? "n/a"}</strong>
              <span>Interpolated Points</span>
              <strong>{data.update_interpolated_points ?? "n/a"}</strong>
              <span>Retries Used</span>
              <strong>{data.update_retries_used ?? "n/a"}</strong>
              <span>Ingest Error</span>
              <strong>{data.update_ingest_error ?? "none"}</strong>
              <span>Forecast</span>
              <strong>{data.update_forecast_name ?? "n/a"}</strong>
              <span>Updated (UTC)</span>
              <strong>{formatUpdateAbsoluteTime(data.update_source_updated_at)}</strong>
            </div>
          </div>

          <div className="metric-grid" style={{ marginTop: 12 }}>
            <div>
              <span className="label">Forecast</span>
              <strong>{data.forecast_name}</strong>
            </div>
            <div>
              <span className="label">Forecast Data Rows</span>
              <strong>{data.forecast_data_count}</strong>
            </div>
            <div>
              <span className="label">Agile Rows (All)</span>
              <strong>{data.agile_points_total}</strong>
            </div>
            <div>
              <span className="label">Agile Rows (Region G)</span>
              <strong>{data.agile_points_region_g}</strong>
            </div>
            <div>
              <span className="label">Day-Ahead Mean</span>
              <strong>{data.day_ahead_mean ?? "n/a"}</strong>
            </div>
            <div>
              <span className="label">Demand Mean</span>
              <strong>{data.demand_mean ?? "n/a"}</strong>
            </div>
          </div>
        </>
      )}
      {parity && (
        <>
          <div className="metric-grid" style={{ marginTop: 12 }}>
            <div>
              <span className="label">Parity Report</span>
              <strong>{parityStatus}</strong>
            </div>
            <div>
              <span className="label">Last Updated</span>
              <strong>{parity.report_updated_at ?? "n/a"}</strong>
            </div>
            <div>
              <span className="label">Report Artifact</span>
              <strong>{parity.report_path ?? "n/a"}</strong>
            </div>
            <div>
              <span className="label">Report SHA-256</span>
              <strong>{parity.report_sha256?.slice(0, 12) ?? "n/a"}</strong>
            </div>
            <div>
              <span className="label">Endpoint Checks</span>
              <strong>{parity.endpoint_count ?? "n/a"}</strong>
            </div>
            <div>
              <span className="label">Data Stats Checks</span>
              <strong>{parity.data_stats_check_count ?? "n/a"}</strong>
            </div>
            <div>
              <span className="label">Failures</span>
              <strong>{parity.failure_count ?? "n/a"}</strong>
            </div>
            <div>
              <span className="label">Min Common Points</span>
              <strong>{parity.min_common_points ?? "n/a"}</strong>
            </div>
            <div>
              <span className="label">Worst Mean Abs Diff</span>
              <strong>{parity.worst_mean_abs_diff ?? "n/a"}</strong>
            </div>
            <div>
              <span className="label">Worst Max Abs Diff</span>
              <strong>{parity.worst_max_abs_diff ?? "n/a"}</strong>
            </div>
            <div>
              <span className="label">Worst P95 Abs Diff</span>
              <strong>{parity.worst_p95_abs_diff ?? "n/a"}</strong>
            </div>
          </div>
          {parity.thresholds && (
            <div className="parity-detail-card">
              <h3>Parity Thresholds</h3>
              <div className="threshold-list">
                {Object.entries(parity.thresholds).map(([key, value]) => (
                  <span key={key} className="threshold-chip">
                    {key}: {value}
                  </span>
                ))}
              </div>
            </div>
          )}
          {parity.failures.length > 0 && (
            <div className="parity-detail-card">
              <h3>Parity Failures</h3>
              <ul className="failure-list">
                {parity.failures.map((failure) => (
                  <li key={failure}>{failure}</li>
                ))}
              </ul>
            </div>
          )}
          {parityHistory.length > 0 && (
            <div className="parity-detail-card">
              <h3>Recent Parity Runs</h3>
              <p>
                Showing {historyOffset + 1}-{historyOffset + parityHistory.length} of {historyTotal}
              </p>
              <ul className="history-list">
                {parityHistory.map((item) => {
                  const status = item.all_passed ? "PASS" : "FAIL";
                  const hash = item.report_sha256?.slice(0, 8) ?? "n/a";
                  return (
                    <li key={`${item.report_updated_at}-${item.report_sha256}`}>
                      {item.report_updated_at ?? "unknown time"} | {status} | failures: {item.failure_count ?? "n/a"} |
                      hash: {hash}
                    </li>
                  );
                })}
              </ul>
              <div className="controls-row" style={{ marginTop: 10 }}>
                <button
                  type="button"
                  onClick={() => setHistoryOffset((current) => Math.max(0, current - HISTORY_PAGE_SIZE))}
                  disabled={!hasPreviousHistoryPage || actionState === "running"}
                >
                  Previous Runs
                </button>
                <button
                  type="button"
                  onClick={() => setHistoryOffset((current) => current + HISTORY_PAGE_SIZE)}
                  disabled={!hasNextHistoryPage || actionState === "running"}
                >
                  Next Runs
                </button>
              </div>
            </div>
          )}
        </>
      )}
    </section>
  );
}
