import { useEffect, useState } from "react";

import {
  fetchDiscordConfig,
  fetchFeedHealth,
  fetchIngestPipelineHealth,
  fetchLatestDiagnostics,
  fetchMlGpuStatus,
  fetchMlWriteMode,
  fetchPipelineTruthAudit,
  fetchParityHistory,
  fetchLatestParitySummary,
  fetchMlParityScorecard,
  refreshFeedSource,
  runBootstrapForecastBundle,
  runUpdateForecastJob,
  sendDiscordTest,
  setDiscordConfig,
  setMlGpuStatus,
  setMlWriteMode as setMlWriteModeConfig,
} from "./api";
import type {
  DiscordNotificationPreferences,
  IngestPipelineHealth,
  LatestForecastDiagnostics,
  LatestParitySummary,
  MlGpuStatus,
  MlWriteModeStatus,
  MlParityScorecard,
  PipelineTruthAudit,
  ParityHistoryItem,
} from "../../lib/api/types";

const HISTORY_PAGE_SIZE = 5;
const READINESS_TARGET_FEATURE_ROWS = 50;

type GrowthSnapshot = {
  capturedAtMs: number;
  sampleSize: number;
  featureRows: number;
};

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

function formatIsoDate(value: string | null): string {
  if (!value) {
    return "n/a";
  }
  const ts = Date.parse(value);
  if (Number.isNaN(ts)) {
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
  }).format(new Date(ts));
}

function feedStatusClass(status: string): "ok" | "warn" | "error" | "neutral" {
  if (status === "healthy") return "ok";
  if (status === "degraded") return "warn";
  if (status === "stale") return "warn";
  if (status === "error") return "error";
  return "neutral";
}

function feedStatusLabel(status: string): string {
  if (status === "degraded") return "DEGRADED";
  if (status === "inactive") return "INACTIVE";
  if (status === "unknown") return "NOT OBSERVED";
  return status.toUpperCase();
}

function renderTabHealthBadge(state: "ok" | "warn" | "bad" | "loading"): string {
  if (state === "loading") return "…";
  if (state === "ok") return "OK";
  if (state === "warn") return "WARN";
  return "ERR";
}

function minutesSince(value: string | null, nowMs: number): number | null {
  if (!value) {
    return null;
  }
  const ts = Date.parse(value);
  if (Number.isNaN(ts)) {
    return null;
  }
  return Math.max(0, Math.floor((nowMs - ts) / 60000));
}

function freshnessLabel(minutes: number | null): "fresh" | "aging" | "stale" | "missing" {
  if (minutes === null) {
    return "missing";
  }
  if (minutes <= 90) {
    return "fresh";
  }
  if (minutes <= 180) {
    return "aging";
  }
  return "stale";
}

function formatFreshness(minutes: number | null): string {
  if (minutes === null) {
    return "No data";
  }
  return `${minutes} min old`;
}

function progressPct(current: number, target: number): number {
  if (target <= 0) {
    return 0;
  }
  return Math.max(0, Math.min(100, Math.round((current / target) * 100)));
}

function buildSparklinePath(values: number[], width = 360, height = 90, padding = 8): string {
  if (values.length === 0) {
    return "";
  }
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = Math.max(1, max - min);
  const innerW = width - padding * 2;
  const innerH = height - padding * 2;

  return values
    .map((value, index) => {
      const x = padding + (index / Math.max(values.length - 1, 1)) * innerW;
      const y = height - padding - ((value - min) / range) * innerH;
      return `${index === 0 ? "M" : "L"}${x.toFixed(2)},${y.toFixed(2)}`;
    })
    .join(" ");
}

type TabKey = "status" | "ml-model" | "gpu" | "pipeline" | "controls" | "discord" | "feed-health";

const DEFAULT_DISCORD_NOTIFICATIONS: DiscordNotificationPreferences = {
  update_started: true,
  update_success: true,
  update_failure: true,
  parity_alert: true,
  gpu_alert: true,
  daily_digest: true,
  pipeline_staleness: true,
};

export function DiagnosticsPanel() {
  const [activeTab, setActiveTab] = useState<TabKey>("status");
  const [nowMs, setNowMs] = useState(() => Date.now());
  const [data, setData] = useState<LatestForecastDiagnostics | null>(null);
  const [parity, setParity] = useState<LatestParitySummary | null>(null);
  const [scorecard, setScorecard] = useState<MlParityScorecard | null>(null);
  const [gpuStatus, setGpuStatus] = useState<MlGpuStatus | null>(null);
  const [mlWriteMode, setMlWriteMode] = useState<MlWriteModeStatus["mode"]>("deterministic");
  const [parityHistory, setParityHistory] = useState<ParityHistoryItem[]>([]);
  const [pipelineHealth, setPipelineHealth] = useState<IngestPipelineHealth | null>(null);
  const [pipelineTruthAudit, setPipelineTruthAudit] = useState<PipelineTruthAudit | null>(null);
  const [feedHealth, setFeedHealth] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState("");
  const [parityError, setParityError] = useState("");
  const [actionState, setActionState] = useState<"idle" | "running">("idle");
  const [actionMessage, setActionMessage] = useState("");
  const [activeActionKind, setActiveActionKind] = useState<"seed" | "update" | "gpu" | "discord" | null>(null);
  const [seedPoints, setSeedPoints] = useState(48);
  const [seedRegionsInput, setSeedRegionsInput] = useState("X,G");
  const [seedReplaceExisting, setSeedReplaceExisting] = useState(true);
  const [seedWriteAgileData, setSeedWriteAgileData] = useState(true);
  const [historyStatusFilter, setHistoryStatusFilter] = useState<"all" | "pass" | "fail">("all");
  const [historyWindowHours, setHistoryWindowHours] = useState(168);
  const [historyOffset, setHistoryOffset] = useState(0);
  const [historyTotal, setHistoryTotal] = useState(0);
  const [growthSnapshots, setGrowthSnapshots] = useState<GrowthSnapshot[]>([]);
  const [discordWebhookUrl, setDiscordWebhookUrl] = useState("");
  const [discordSaveState, setDiscordSaveState] = useState<"idle" | "saving" | "saved">("idle");
  const [discordTestState, setDiscordTestState] = useState<"idle" | "testing">("idle");
  const [discordNotifications, setDiscordNotifications] = useState<DiscordNotificationPreferences>(
    DEFAULT_DISCORD_NOTIFICATIONS
  );
  const [discordEnabled, setDiscordEnabled] = useState(false);
  const [discordError, setDiscordError] = useState("");
  const [loadingDiagnostics, setLoadingDiagnostics] = useState(true);
  const [loadingParitySummary, setLoadingParitySummary] = useState(true);
  const [loadingScorecard, setLoadingScorecard] = useState(true);
  const [loadingParityHistory, setLoadingParityHistory] = useState(true);
  const [loadingPipeline, setLoadingPipeline] = useState(true);
  const [loadingPipelineTruth, setLoadingPipelineTruth] = useState(true);
  const [loadingGpu, setLoadingGpu] = useState(true);
  const [loadingMlWriteMode, setLoadingMlWriteMode] = useState(true);
  const [loadingFeedHealth, setLoadingFeedHealth] = useState(true);
  const [refreshingFeeds, setRefreshingFeeds] = useState<Record<string, boolean>>({});
  const [loadingDiscord, setLoadingDiscord] = useState(true);
  const [diagnosticsLoadedAt, setDiagnosticsLoadedAt] = useState<string | null>(null);
  const [parityLoadedAt, setParityLoadedAt] = useState<string | null>(null);
  const [scorecardLoadedAt, setScorecardLoadedAt] = useState<string | null>(null);
  const [parityHistoryLoadedAt, setParityHistoryLoadedAt] = useState<string | null>(null);
  const [pipelineLoadedAt, setPipelineLoadedAt] = useState<string | null>(null);
  const [pipelineTruthLoadedAt, setPipelineTruthLoadedAt] = useState<string | null>(null);
  const [gpuLoadedAt, setGpuLoadedAt] = useState<string | null>(null);
  const [feedHealthLoadedAt, setFeedHealthLoadedAt] = useState<string | null>(null);
  const [discordLoadedAt, setDiscordLoadedAt] = useState<string | null>(null);

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

  function formatSectionLoadedAt(value: string | null): string {
    if (!value) {
      return "Not loaded yet";
    }
    return `Updated ${formatUpdateRelativeTime(value, nowMs)}`;
  }

  async function refreshDiagnostics() {
    setLoadingDiagnostics(true);
    try {
      const result = await fetchLatestDiagnostics();
      setData(result);
      setError("");
      setDiagnosticsLoadedAt(new Date().toISOString());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed loading diagnostics");
    } finally {
      setLoadingDiagnostics(false);
    }
  }

  async function refreshParitySummary() {
    setLoadingParitySummary(true);
    try {
      const result = await fetchLatestParitySummary();
      setParity(result);
      setParityError("");
      setParityLoadedAt(new Date().toISOString());
    } catch (err) {
      setParityError(err instanceof Error ? err.message : "Failed loading parity summary");
    } finally {
      setLoadingParitySummary(false);
    }
  }

  async function refreshScorecard() {
    setLoadingScorecard(true);
    try {
      const scorecardResult = await fetchMlParityScorecard(30);
      setScorecard(scorecardResult);
      setParityError("");
      setScorecardLoadedAt(new Date().toISOString());
    } catch (err) {
      setParityError(err instanceof Error ? err.message : "Failed loading parity scorecard");
    } finally {
      setLoadingScorecard(false);
    }
  }

  async function refreshParityHistory() {
    setLoadingParityHistory(true);
    try {
      const parityHistoryResult = await fetchParityHistory({
        limit: HISTORY_PAGE_SIZE,
        offset: historyOffset,
        status: historyStatusFilter === "all" ? undefined : historyStatusFilter,
        since: buildHistorySinceIso(),
      });
      setParityHistory(parityHistoryResult.items);
      setHistoryTotal(parityHistoryResult.total);
      setParityError("");
      setParityHistoryLoadedAt(new Date().toISOString());
    } catch (err) {
      setParityError(err instanceof Error ? err.message : "Failed loading parity history");
    } finally {
      setLoadingParityHistory(false);
    }
  }

  async function refreshPipelineHealth() {
    setLoadingPipeline(true);
    try {
      const healthResult = await fetchIngestPipelineHealth();
      setPipelineHealth(healthResult);
      setParityError("");
      setPipelineLoadedAt(new Date().toISOString());
    } catch (err) {
      setParityError(err instanceof Error ? err.message : "Failed loading pipeline health");
    } finally {
      setLoadingPipeline(false);
    }
  }

  async function refreshPipelineTruthAudit() {
    setLoadingPipelineTruth(true);
    try {
      const result = await fetchPipelineTruthAudit();
      setPipelineTruthAudit(result);
      setParityError("");
      setPipelineTruthLoadedAt(new Date().toISOString());
    } catch (err) {
      setParityError(err instanceof Error ? err.message : "Failed loading pipeline truth audit");
    } finally {
      setLoadingPipelineTruth(false);
    }
  }

  async function refreshGpuStatus() {
    setLoadingGpu(true);
    try {
      const gpuResult = await fetchMlGpuStatus();
      setGpuStatus(gpuResult);
      setParityError("");
      setGpuLoadedAt(new Date().toISOString());
    } catch (err) {
      setParityError(err instanceof Error ? err.message : "Failed loading GPU status");
    } finally {
      setLoadingGpu(false);
    }
  }

  async function refreshMlWriteMode() {
    setLoadingMlWriteMode(true);
    try {
      const modeResult = await fetchMlWriteMode();
      setMlWriteMode(modeResult.mode);
      setParityError("");
    } catch (err) {
      setParityError(err instanceof Error ? err.message : "Failed loading ML write mode");
    } finally {
      setLoadingMlWriteMode(false);
    }
  }

  async function refreshFeedHealth() {
    setLoadingFeedHealth(true);
    try {
      const feedHealthResult = await fetchFeedHealth();
      setFeedHealth(feedHealthResult);
      setParityError("");
      setFeedHealthLoadedAt(new Date().toISOString());
    } catch (err) {
      setParityError(err instanceof Error ? err.message : "Failed loading feed health");
    } finally {
      setLoadingFeedHealth(false);
    }
  }

  async function refreshDiscordConfig() {
    setLoadingDiscord(true);
    try {
      const result = await fetchDiscordConfig();
      setDiscordWebhookUrl(result.webhook_url ?? "");
      setDiscordNotifications(result.notifications);
      setDiscordEnabled(result.enabled);
      setDiscordSaveState("idle");
      setDiscordError("");
      setDiscordLoadedAt(new Date().toISOString());
    } catch (err) {
      setDiscordError(err instanceof Error ? err.message : "Failed loading Discord configuration");
    } finally {
      setLoadingDiscord(false);
    }
  }

  async function refreshAll() {
    await Promise.all([
      refreshDiagnostics(),
      refreshParitySummary(),
      refreshScorecard(),
      refreshParityHistory(),
      refreshPipelineHealth(),
      refreshPipelineTruthAudit(),
      refreshGpuStatus(),
      refreshMlWriteMode(),
      refreshFeedHealth(),
      refreshDiscordConfig(),
    ]);
  }

  useEffect(() => {
    void refreshDiagnostics();
    void refreshParitySummary();
    void refreshScorecard();
    void refreshParityHistory();
    void refreshPipelineHealth();
    void refreshPipelineTruthAudit();
    void refreshGpuStatus();
    void refreshMlWriteMode();
    void refreshFeedHealth();
    void refreshDiscordConfig();
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

  useEffect(() => {
    const timer = window.setInterval(() => {
      void refreshAll();
    }, 60000);

    return () => {
      window.clearInterval(timer);
    };
  }, [historyOffset, historyStatusFilter, historyWindowHours]);

  useEffect(() => {
    if (!data || !scorecard) {
      return;
    }

    setGrowthSnapshots((previous) => {
      const next: GrowthSnapshot = {
        capturedAtMs: Date.now(),
        sampleSize: scorecard.sample_size,
        featureRows: data.forecast_data_count,
      };

      const last = previous[previous.length - 1];
      if (last && last.sampleSize === next.sampleSize && last.featureRows === next.featureRows) {
        return previous;
      }
      return [...previous, next].slice(-24);
    });
  }, [data, scorecard]);

  const hasPreviousHistoryPage = historyOffset > 0;
  const hasNextHistoryPage = historyOffset + parityHistory.length < historyTotal;

  const sampleTarget = scorecard?.window_size ?? 30;
  const sampleCurrent = scorecard?.sample_size ?? 0;
  const featureCurrent = data?.forecast_data_count ?? 0;
  const updateCurrent = data?.update_records_written && data.update_records_written > 0 ? 1 : 0;

  const readinessRows = [
    {
      label: "ML samples",
      current: sampleCurrent,
      target: sampleTarget,
      pct: progressPct(sampleCurrent, sampleTarget),
    },
    {
      label: "Feature rows",
      current: featureCurrent,
      target: READINESS_TARGET_FEATURE_ROWS,
      pct: progressPct(featureCurrent, READINESS_TARGET_FEATURE_ROWS),
    },
    {
      label: "Update output",
      current: updateCurrent,
      target: 1,
      pct: progressPct(updateCurrent, 1),
    },
  ];

  const readinessComplete = readinessRows.every((row) => row.current >= row.target);

  const freshnessRows = [
    {
      label: "Update run",
      minutes: minutesSince(data?.update_source_updated_at ?? null, nowMs),
    },
    {
      label: "Latest forecast",
      minutes: minutesSince(
        data?.forecast_name === "bundle::update-job-seed"
          ? data?.update_source_updated_at ?? data?.created_at ?? null
          : data?.created_at ?? null,
        nowMs,
      ),
    },
    {
      label: "Parity report",
      minutes: minutesSince(parity?.report_updated_at ?? null, nowMs),
    },
  ].map((row) => {
    const state = freshnessLabel(row.minutes);
    return {
      ...row,
      state,
      display: formatFreshness(row.minutes),
    };
  });

  const samplesPath = buildSparklinePath(growthSnapshots.map((s) => s.sampleSize));
  const featuresPath = buildSparklinePath(growthSnapshots.map((s) => s.featureRows));
  const trendHasData = growthSnapshots.length >= 2;

  const trustedCustomerForecastAvailable =
    data?.update_source === "ml" && data?.update_ml_write_mode === "ml" && !data?.training_mode;

  const customerAvailabilityVerdict: { state: "pass" | "fail" | "pending"; title: string; detail: string } = (() => {
    if (loadingDiagnostics || !data) {
      return {
        state: "pending",
        title: "Customer Availability: CHECKING",
        detail: "Refreshing latest update-state signals...",
      };
    }

    if (trustedCustomerForecastAvailable) {
      return {
        state: "pass",
        title: "Customer Availability: ENABLED",
        detail: "Customer-facing forecast endpoints are backed by trusted ML output.",
      };
    }

    if (data.training_mode || data.update_ml_write_mode === "deterministic") {
      return {
        state: "fail",
        title: "Customer Availability: DISABLED",
        detail: "Last update ran in training or deterministic mode, so customer forecasts are intentionally blocked.",
      };
    }

    if (data.update_ml_write_mode === "shadow") {
      return {
        state: "fail",
        title: "Customer Availability: DISABLED",
        detail: "Last update ran in shadow mode, which does not permit customer-facing forecast output.",
      };
    }

    if (data.update_ml_error) {
      return {
        state: "fail",
        title: "Customer Availability: DISABLED",
        detail: `ML write is not trusted: ${data.update_ml_error}`,
      };
    }

    return {
      state: "fail",
      title: "Customer Availability: DISABLED",
      detail: `Last update source was ${data.update_source ?? "unknown"}, not trusted ML output.`,
    };
  })();

  const statusTabHealth: "ok" | "warn" | "bad" | "loading" =
    loadingDiagnostics ? "loading" : error ? "bad" : customerAvailabilityVerdict.state === "fail" ? "warn" : "ok";

  const mlTabHealth: "ok" | "warn" | "bad" | "loading" =
    loadingParitySummary || loadingScorecard
      ? "loading"
      : parityError
        ? "bad"
        : parity?.all_passed === false
          ? "warn"
          : "ok";

  const gpuTabHealth: "ok" | "warn" | "bad" | "loading" =
    loadingGpu ? "loading" : gpuStatus?.compatible === false ? "warn" : "ok";

  const pipelineTabHealth: "ok" | "warn" | "bad" | "loading" =
    loadingPipeline || loadingPipelineTruth
      ? "loading"
      : pipelineTruthAudit?.trust_level === "low"
        ? "bad"
        : pipelineTruthAudit?.trust_level === "medium"
          ? "warn"
      : pipelineHealth?.all_sources_healthy === false
        ? "warn"
        : "ok";

  const discordTabHealth: "ok" | "warn" | "bad" | "loading" =
    loadingDiscord ? "loading" : discordError ? "bad" : discordEnabled ? "ok" : "warn";

  const feedHealthTabHealth: "ok" | "warn" | "bad" | "loading" = (() => {
    if (loadingFeedHealth) return "loading";
    if (!feedHealth) return "warn";
    const entries = Object.values(feedHealth) as Array<{ status?: string }>;
    if (entries.some((entry) => entry.status === "error")) return "bad";
    if (entries.some((entry) => entry.status === "degraded" || entry.status === "stale")) return "warn";
    return "ok";
  })();

  const truthIssues = pipelineTruthAudit?.issues ?? [];
  const truthIssueCodes = new Set(truthIssues.map((issue) => issue.code));
  const hasHighOrCriticalTruthIssue = truthIssues.some((issue) => {
    const severity = issue.severity.toLowerCase();
    return severity === "high" || severity === "critical";
  });
  const truthVerdict: { state: "pass" | "fail" | "pending"; title: string; detail: string } = (() => {
    if (loadingPipelineTruth || !pipelineTruthAudit) {
      return {
        state: "pending",
        title: "Deploy Verdict: CHECKING",
        detail: "Refreshing persisted forecast integrity signals...",
      };
    }

    if (truthIssueCodes.has("missing_day_ahead_values")) {
      return {
        state: "fail",
        title: "Deploy Verdict: FAIL",
        detail: "Operational forecast has no non-null day-ahead values.",
      };
    }

    if (pipelineTruthAudit.latest_forecast_rows < 48) {
      return {
        state: "fail",
        title: "Deploy Verdict: FAIL",
        detail: "Latest operational forecast row count is below the minimum threshold.",
      };
    }

    if (
      pipelineTruthAudit.latest_data_freshness_minutes !== null &&
      pipelineTruthAudit.latest_data_freshness_minutes > 240
    ) {
      return {
        state: "fail",
        title: "Deploy Verdict: FAIL",
        detail: "Operational forecast data is stale.",
      };
    }

    if (pipelineTruthAudit.trust_level === "low") {
      return {
        state: "fail",
        title: "Deploy Verdict: FAIL",
        detail: "Truth audit trust level is low.",
      };
    }

    if (hasHighOrCriticalTruthIssue) {
      const firstBlockingIssue = truthIssues.find((issue) => {
        const severity = issue.severity.toLowerCase();
        return severity === "high" || severity === "critical";
      });
      return {
        state: "fail",
        title: "Deploy Verdict: FAIL",
        detail: firstBlockingIssue
          ? `${firstBlockingIssue.severity.toUpperCase()}: ${firstBlockingIssue.detail}`
          : "Truth audit reported blocking issues.",
      };
    }

    return {
      state: "pass",
      title: "Deploy Verdict: PASS",
      detail: "Truth audit does not indicate blocking persisted-data integrity issues.",
    };
  })();

  async function handleSeedBundle() {
    setActiveActionKind("seed");
    setActionState("running");
    setActionMessage("Seeding bundle. This usually completes quickly.");
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
    setActiveActionKind("update");
    setActionState("running");
    setActionMessage("Running update job. This can take a minute while ingest, ML, write, and parity complete.");
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

  async function handleGpuToggle(enabled: boolean) {
    setActiveActionKind("gpu");
    setActionState("running");
    setActionMessage("");
    try {
      const nextStatus = await setMlGpuStatus(enabled);
      setGpuStatus(nextStatus);
      setActionMessage(
        nextStatus.active
          ? "GPU acceleration enabled and compatible."
          : "GPU acceleration setting saved, but compatibility test is failing. Running on CPU."
      );
    } catch (err) {
      setActionMessage(err instanceof Error ? err.message : "Failed to update GPU setting");
    } finally {
      setActionState("idle");
    }
  }

  async function handleMlWriteModeChange(mode: MlWriteModeStatus["mode"]) {
    setActiveActionKind("update");
    setActionState("running");
    setActionMessage("");
    try {
      const nextMode = await setMlWriteModeConfig(mode);
      setMlWriteMode(nextMode.mode);
      setActionMessage(`ML write mode set to ${nextMode.mode}.`);
      await refreshDiagnostics();
      await refreshScorecard();
    } catch (err) {
      setActionMessage(err instanceof Error ? err.message : "Failed to update ML write mode");
    } finally {
      setActionState("idle");
    }
  }

  async function handleSaveDiscordConfig() {
    setActiveActionKind("discord");
    setDiscordSaveState("saving");
    setActionMessage("");
    try {
      const result = await setDiscordConfig({
        webhook_url: discordWebhookUrl.trim() || null,
        notifications: discordNotifications,
      });
      setDiscordWebhookUrl(result.webhook_url ?? "");
      setDiscordNotifications(result.notifications);
      setDiscordEnabled(result.enabled);
      setDiscordSaveState("saved");
      setDiscordError("");
      setActionMessage(result.enabled ? "Discord notification settings saved." : "Discord notifications disabled.");
    } catch (err) {
      setDiscordSaveState("idle");
      setActionMessage(err instanceof Error ? err.message : "Failed saving Discord settings");
    }
  }

  async function handleSendDiscordTest() {
    setActiveActionKind("discord");
    setDiscordTestState("testing");
    setActionMessage("");
    try {
      const result = await sendDiscordTest();
      setActionMessage(result.detail);
      setDiscordError("");
    } catch (err) {
      setActionMessage(err instanceof Error ? err.message : "Failed sending Discord test notification");
    } finally {
      setDiscordTestState("idle");
    }
  }

  async function handleRefreshFeedSource(sourceId: string, sourceName: string) {
    setRefreshingFeeds((current) => ({ ...current, [sourceId]: true }));
    setActionMessage(`Refreshing ${sourceName}...`);
    try {
      const result = await refreshFeedSource(sourceId);
      await refreshFeedHealth();
      setActionMessage(`${result.detail} (${result.records_received} records received)`);
    } catch (err) {
      setActionMessage(err instanceof Error ? err.message : `Failed refreshing ${sourceName}`);
    } finally {
      setRefreshingFeeds((current) => ({ ...current, [sourceId]: false }));
    }
  }

  function handleDiscordNotificationToggle(key: keyof DiscordNotificationPreferences, checked: boolean) {
    setDiscordNotifications((current) => ({
      ...current,
      [key]: checked,
    }));
    setDiscordSaveState("idle");
  }

  return (
    <section className="card">
      <h2>Diagnostics</h2>
      
      {/* Tab Navigation */}
      <div className="tab-navigation">
        <button
          type="button"
          className={`tab-button ${activeTab === "status" ? "active" : ""}`}
          onClick={() => setActiveTab("status")}
        >
          Status
          <span className={`tab-health-chip ${statusTabHealth}`}>{renderTabHealthBadge(statusTabHealth)}</span>
        </button>
        <button
          type="button"
          className={`tab-button ${activeTab === "ml-model" ? "active" : ""}`}
          onClick={() => setActiveTab("ml-model")}
        >
          ML Model
          <span className={`tab-health-chip ${mlTabHealth}`}>{renderTabHealthBadge(mlTabHealth)}</span>
        </button>
        <button
          type="button"
          className={`tab-button ${activeTab === "gpu" ? "active" : ""}`}
          onClick={() => setActiveTab("gpu")}
        >
          GPU Acceleration
          <span className={`tab-health-chip ${gpuTabHealth}`}>{renderTabHealthBadge(gpuTabHealth)}</span>
        </button>
        <button
          type="button"
          className={`tab-button ${activeTab === "pipeline" ? "active" : ""}`}
          onClick={() => setActiveTab("pipeline")}
        >
          Data Pipeline
          <span className={`tab-health-chip ${pipelineTabHealth}`}>{renderTabHealthBadge(pipelineTabHealth)}</span>
        </button>
        <button
          type="button"
          className={`tab-button ${activeTab === "controls" ? "active" : ""}`}
          onClick={() => setActiveTab("controls")}
        >
          Controls
        </button>
        <button
          type="button"
          className={`tab-button ${activeTab === "discord" ? "active" : ""}`}
          onClick={() => setActiveTab("discord")}
        >
          Discord
          <span className={`tab-health-chip ${discordTabHealth}`}>{renderTabHealthBadge(discordTabHealth)}</span>
        </button>
        <button
          type="button"
          className={`tab-button ${activeTab === "feed-health" ? "active" : ""}`}
          onClick={() => setActiveTab("feed-health")}
        >
          Feed Health
          <span className={`tab-health-chip ${feedHealthTabHealth}`}>{renderTabHealthBadge(feedHealthTabHealth)}</span>
        </button>
      </div>

      {/* Global Messages */}
      {actionMessage && <p>{actionMessage}</p>}
      {error && <p>Diagnostics unavailable: {error}</p>}
      {parityError && <p>Parity summary unavailable: {parityError}</p>}
      {discordError && <p>Discord configuration unavailable: {discordError}</p>}
      {!error && !data && <p>Loading diagnostics...</p>}
      <div className="loading-chip-row">
        {loadingDiagnostics && <span className="loading-chip">Summary loading...</span>}
        {loadingParitySummary && <span className="loading-chip">Parity loading...</span>}
        {loadingScorecard && <span className="loading-chip">Scorecard loading...</span>}
        {loadingParityHistory && <span className="loading-chip">History loading...</span>}
        {loadingPipeline && <span className="loading-chip">Pipeline loading...</span>}
        {loadingGpu && <span className="loading-chip">GPU loading...</span>}
        {loadingFeedHealth && <span className="loading-chip">Feed health loading...</span>}
        {loadingDiscord && <span className="loading-chip">Discord loading...</span>}
      </div>

      {/* STATUS TAB */}
      {activeTab === "status" && (
        <>
          {(data || scorecard || loadingDiagnostics || loadingScorecard) && (
            <div className={`growth-visibility-card progressive-card ${loadingDiagnostics || loadingScorecard ? "is-loading" : "is-ready"}`}>
              <div className="chart-header">
                <h3>Data Growth and Readiness</h3>
                <div className="header-badges">
                  <span className={readinessComplete ? "growth-badge ready" : "growth-badge warming"}>
                    {readinessComplete ? "READY" : "WARMING UP"}
                  </span>
                  <span className={`section-health-pill ${statusTabHealth}`}>{renderTabHealthBadge(statusTabHealth)}</span>
                </div>
              </div>
              <p className="section-meta">{formatSectionLoadedAt(scorecardLoadedAt || diagnosticsLoadedAt)}</p>

              <div className="progress-stack">
                {readinessRows.map((row) => (
                  <div key={row.label}>
                    <div className="progress-label-row">
                      <span>{row.label}</span>
                      <span>
                        {row.current} / {row.target}
                      </span>
                    </div>
                    <div className="progress-track">
                      <div className="progress-fill" style={{ width: `${row.pct}%` }} />
                    </div>
                  </div>
                ))}
              </div>

              <div className="freshness-grid">
                {freshnessRows.map((row) => (
                  <div key={row.label} className="freshness-item">
                    <span className="label">{row.label}</span>
                    <strong>{row.display}</strong>
                    <span className={`freshness-pill ${row.state}`}>{row.state.toUpperCase()}</span>
                  </div>
                ))}
              </div>

              <div className="growth-trend-card">
                <div className="chart-header">
                  <h3>Growth Since Page Open</h3>
                  <span>{growthSnapshots.length} snapshots</span>
                </div>
                {trendHasData ? (
                  <svg viewBox="0 0 360 90" className="growth-sparkline" role="img" aria-label="Growth trends">
                    <path d={samplesPath} className="sparkline-primary" />
                    <path d={featuresPath} className="sparkline-secondary" />
                  </svg>
                ) : (
                  <p>Collecting trend points...</p>
                )}
                <p className="growth-legend">Orange: ML samples. Blue: feature rows.</p>
              </div>
            </div>
          )}

          {(data || loadingDiagnostics) && (
            <>
              <div className={`truth-verdict-banner ${customerAvailabilityVerdict.state}`}>
                <strong>{customerAvailabilityVerdict.title}</strong>
                <span>{customerAvailabilityVerdict.detail}</span>
              </div>

              <div className={`update-run-card progressive-card ${loadingDiagnostics ? "is-loading" : "is-ready"}`}>
                <h3>Last Update Run</h3>
                <div className="update-run-topline">
                  {loadingDiagnostics ? "Loading latest run..." : formatUpdateRelativeTime(data?.update_source_updated_at ?? null, nowMs)}
                </div>
                {loadingDiagnostics && (
                  <div className="skeleton-stack">
                    <div className="skeleton-line long" />
                    <div className="skeleton-line" />
                    <div className="skeleton-line short" />
                  </div>
                )}
                <div className="update-run-grid">
                  <span>Source</span>
                  <strong>{data?.update_source ?? "n/a"}</strong>
                  <span>Records</span>
                  <strong>{data?.update_records_written ?? "n/a"}</strong>
                  <span>Day-Ahead Points</span>
                  <strong>{data?.update_day_ahead_points ?? "n/a"}</strong>
                  <span>Raw Points</span>
                  <strong>{data?.update_raw_points ?? "n/a"}</strong>
                  <span>Aligned Points</span>
                  <strong>{data?.update_aligned_points ?? "n/a"}</strong>
                  <span>Interpolated Points</span>
                  <strong>{data?.update_interpolated_points ?? "n/a"}</strong>
                  <span>Retries Used</span>
                  <strong>{data?.update_retries_used ?? "n/a"}</strong>
                  <span>Ingest Error</span>
                  <strong>{data?.update_ingest_error ?? "none"}</strong>
                  <span>Forecast</span>
                  <strong>{data?.update_forecast_name ?? "n/a"}</strong>
                  <span>Updated (UTC)</span>
                  <strong>{formatUpdateAbsoluteTime(data?.update_source_updated_at ?? null)}</strong>
                </div>
              </div>

              <div className="metric-grid" style={{ marginTop: 12 }}>
                <div>
                  <span className="label">Forecast</span>
                  <strong>{data?.forecast_name ?? "n/a"}</strong>
                </div>
                <div>
                  <span className="label">Forecast Data Rows</span>
                  <strong>{data?.forecast_data_count ?? "n/a"}</strong>
                </div>
                <div>
                  <span className="label">Agile Rows (All)</span>
                  <strong>{data?.agile_points_total ?? "n/a"}</strong>
                </div>
                <div>
                  <span className="label">Agile Rows (Region G)</span>
                  <strong>{data?.agile_points_region_g ?? "n/a"}</strong>
                </div>
                <div>
                  <span className="label">Day-Ahead Mean</span>
                  <strong>{data?.day_ahead_mean ?? "n/a"}</strong>
                </div>
                <div>
                  <span className="label">Demand Mean</span>
                  <strong>{data?.demand_mean ?? "n/a"}</strong>
                </div>
              </div>
            </>
          )}
        </>
      )}

      {/* ML MODEL TAB */}
      {activeTab === "ml-model" && (
        <>
          {(loadingParitySummary || loadingScorecard || loadingParityHistory) && (
            <div className="parity-detail-card">
              <p className="section-loading">Loading ML and parity sections...</p>
            </div>
          )}
          {parity && scorecard && (
            <div className={`parity-detail-card progressive-card ${loadingParitySummary || loadingScorecard ? "is-loading" : "is-ready"}`}>
              <div className="chart-header" style={{ marginBottom: 2 }}>
                <h3>ML Parity Scorecard</h3>
                <span className={`section-health-pill ${mlTabHealth}`}>{renderTabHealthBadge(mlTabHealth)}</span>
              </div>
              <p className="section-meta">{formatSectionLoadedAt(scorecardLoadedAt || parityLoadedAt)}</p>
              <div className="scorecard-header-row">
                <span className={`scorecard-confidence ${scorecard.confidence_label}`}>
                  {scorecard.confidence_label.toUpperCase()} confidence
                </span>
                <strong>{scorecard.confidence_percent.toFixed(2)}%</strong>
              </div>
              <div className="metric-grid" style={{ marginTop: 10 }}>
                <div>
                  <span className="label">Effective Mode</span>
                  <strong>{scorecard.effective_mode}</strong>
                </div>
                <div>
                  <span className="label">Configured Mode</span>
                  <strong>{scorecard.configured_write_mode ?? "n/a"}</strong>
                </div>
                <div>
                  <span className="label">Training Mode</span>
                  <strong>{scorecard.training_mode ? "yes" : "no"}</strong>
                </div>
                <div>
                  <span className="label">Sample Size</span>
                  <strong>
                    {scorecard.sample_size} / {scorecard.window_size}
                  </strong>
                </div>
                <div>
                  <span className="label">Rolling MAE</span>
                  <strong>{scorecard.rolling_mae_vs_deterministic ?? "n/a"}</strong>
                </div>
                <div>
                  <span className="label">Rolling P95 Abs</span>
                  <strong>{scorecard.rolling_p95_abs_vs_deterministic ?? "n/a"}</strong>
                </div>
                <div>
                  <span className="label">Rolling Max Abs</span>
                  <strong>{scorecard.rolling_max_abs_vs_deterministic ?? "n/a"}</strong>
                </div>
                <div>
                  <span className="label">Latest Error</span>
                  <strong>{scorecard.latest_error ?? "none"}</strong>
                </div>
              </div>
            </div>
          )}

          {data && (
            <div className="parity-detail-card">
              <h3>ML Update Job Details</h3>
              <div className="metric-grid" style={{ marginTop: 10 }}>
                <div>
                  <span className="label">Device Used</span>
                  <strong>{data.update_ml_device_used ?? "n/a"}</strong>
                </div>
                <div>
                  <span className="label">Write Mode</span>
                  <strong>{data.update_ml_write_mode ?? "n/a"}</strong>
                </div>
                <div>
                  <span className="label">Training Rows</span>
                  <strong>{data.update_ml_training_rows ?? "n/a"}</strong>
                </div>
                <div>
                  <span className="label">Test Rows</span>
                  <strong>{data.update_ml_test_rows ?? "n/a"}</strong>
                </div>
                <div>
                  <span className="label">CV Mean RMSE</span>
                  <strong>{data.update_ml_cv_mean_rmse ?? "n/a"}</strong>
                </div>
                <div>
                  <span className="label">CV Stdev RMSE</span>
                  <strong>{data.update_ml_cv_stdev_rmse ?? "n/a"}</strong>
                </div>
                <div>
                  <span className="label">Candidate Points</span>
                  <strong>{data.update_ml_candidate_points ?? "n/a"}</strong>
                </div>
                <div>
                  <span className="label">Compare MAE</span>
                  <strong>{data.update_ml_compare_mae ?? "n/a"}</strong>
                </div>
                <div>
                  <span className="label">Compare Max Abs</span>
                  <strong>{data.update_ml_compare_max_abs ?? "n/a"}</strong>
                </div>
                <div>
                  <span className="label">Compare P95 Abs</span>
                  <strong>{data.update_ml_compare_p95_abs ?? "n/a"}</strong>
                </div>
              </div>
            </div>
          )}

          {parity && (
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
          )}

          {parity && parity.thresholds && (
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

          {parity && parity.failures.length > 0 && (
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

      {/* GPU ACCELERATION TAB */}
      {activeTab === "gpu" && (
        <>
          {loadingGpu && (
            <div className="parity-detail-card">
              <p className="section-loading">Loading GPU status...</p>
              <div className="skeleton-stack">
                <div className="skeleton-line" />
                <div className="skeleton-line long" />
              </div>
            </div>
          )}
          {gpuStatus && (
            <div className={`parity-detail-card progressive-card ${loadingGpu ? "is-loading" : "is-ready"}`}>
              <div className="chart-header" style={{ marginBottom: 2 }}>
                <h3>ML GPU Acceleration Status</h3>
                <span className={`section-health-pill ${gpuTabHealth}`}>{renderTabHealthBadge(gpuTabHealth)}</span>
              </div>
              <p className="section-meta">{formatSectionLoadedAt(gpuLoadedAt)}</p>
              <div className="metric-grid" style={{ marginTop: 10 }}>
                <div>
                  <span className="label">Tested</span>
                  <strong>{gpuStatus.tested ? "yes" : "no"}</strong>
                </div>
                <div>
                  <span className="label">Compatible</span>
                  <strong>{gpuStatus.compatible ? "yes" : "no"}</strong>
                </div>
                <div>
                  <span className="label">Active Device</span>
                  <strong>{gpuStatus.active ? "GPU (CUDA)" : "CPU"}</strong>
                </div>
                <div>
                  <span className="label">GPU Name</span>
                  <strong>{gpuStatus.gpu_name ?? "n/a"}</strong>
                </div>
                <div>
                  <span className="label">XGBoost Version</span>
                  <strong>{gpuStatus.xgboost_version ?? "n/a"}</strong>
                </div>
                <div>
                  <span className="label">Last Test (UTC)</span>
                  <strong>{formatIsoDate(gpuStatus.tested_at)}</strong>
                </div>
              </div>
              {gpuStatus.reason && <p className="gpu-reason">Compatibility note: {gpuStatus.reason}</p>}
            </div>
          )}

          <div className="parity-detail-card">
            <h3>GPU Training Control</h3>
            <div className="controls-row" style={{ marginTop: 12 }}>
              <label>
                GPU Training
                <select
                  value={gpuStatus?.enabled ? "enabled" : "disabled"}
                  onChange={(event) => void handleGpuToggle(event.target.value === "enabled")}
                  disabled={actionState === "running"}
                >
                  <option value="disabled">Disabled (CPU only)</option>
                  <option value="enabled">Enabled (GPU if compatible)</option>
                </select>
              </label>
            </div>
          </div>
        </>
      )}

      {/* DATA PIPELINE TAB */}
      {activeTab === "pipeline" && (
        <>
          <div className={`truth-verdict-banner ${truthVerdict.state}`}>
            <strong>{truthVerdict.title}</strong>
            <span>{truthVerdict.detail}</span>
          </div>

          {(loadingPipelineTruth || pipelineTruthAudit) && (
            <div className={`pipeline-health-card progressive-card ${loadingPipelineTruth ? "is-loading" : "is-ready"}`}>
              <div className="chart-header">
                <h3>Pipeline Truth Audit</h3>
                <span
                  className={`section-health-pill ${
                    pipelineTruthAudit?.trust_level === "high"
                      ? "ok"
                      : pipelineTruthAudit?.trust_level === "medium"
                        ? "warn"
                        : "bad"
                  }`}
                >
                  {pipelineTruthAudit ? `TRUST ${pipelineTruthAudit.trust_level.toUpperCase()}` : "AUDITING..."}
                </span>
              </div>
              <p className="section-meta">{formatSectionLoadedAt(pipelineTruthLoadedAt)}</p>
              {loadingPipelineTruth && <p className="section-loading">Auditing persisted forecast truth signals...</p>}

              {pipelineTruthAudit && (
                <>
                  <div className="metric-grid" style={{ marginTop: 10 }}>
                    <div>
                      <span className="label">Latest Forecast Rows</span>
                      <strong>{pipelineTruthAudit.latest_forecast_rows}</strong>
                    </div>
                    <div>
                      <span className="label">Unique Slots</span>
                      <strong>{pipelineTruthAudit.latest_unique_slots}</strong>
                    </div>
                    <div>
                      <span className="label">Duplicate Slots</span>
                      <strong>{pipelineTruthAudit.latest_duplicate_slots}</strong>
                    </div>
                    <div>
                      <span className="label">Zero Day-Ahead Ratio</span>
                      <strong>
                        {pipelineTruthAudit.latest_day_ahead_zero_ratio === null
                          ? "n/a"
                          : `${(pipelineTruthAudit.latest_day_ahead_zero_ratio * 100).toFixed(1)}%`}
                      </strong>
                    </div>
                    <div>
                      <span className="label">Last Seen (UTC)</span>
                      <strong>{formatIsoDate(pipelineTruthAudit.latest_data_last_seen)}</strong>
                    </div>
                    <div>
                      <span className="label">Freshness (min)</span>
                      <strong>{pipelineTruthAudit.latest_data_freshness_minutes ?? "n/a"}</strong>
                    </div>
                  </div>

                  {pipelineTruthAudit.issues.length > 0 ? (
                    <ul className="history-list" style={{ marginTop: 10 }}>
                      {pipelineTruthAudit.issues.map((issue) => (
                        <li key={issue.code}>{issue.severity.toUpperCase()}: {issue.detail}</li>
                      ))}
                    </ul>
                  ) : (
                    <p className="section-loading" style={{ marginTop: 10 }}>No integrity issues detected in latest persisted forecast.</p>
                  )}
                </>
              )}
            </div>
          )}

          {loadingPipeline && (
            <div className="pipeline-health-card">
              <p className="section-loading">Loading pipeline health...</p>
              <div className="skeleton-stack">
                <div className="skeleton-line long" />
                <div className="skeleton-line" />
              </div>
            </div>
          )}
          {pipelineHealth && (
            <div className={`pipeline-health-card progressive-card ${loadingPipeline ? "is-loading" : "is-ready"}`}>
              <div className="chart-header">
                <h3>Pipeline Health (End-to-End)</h3>
                <div className="header-badges">
                  <span className={pipelineHealth.all_sources_healthy ? "growth-badge ready" : "growth-badge warming"}>
                    Sources healthy: {pipelineHealth.healthy_source_count}/{pipelineHealth.expected_source_count}
                  </span>
                  <span className={`section-health-pill ${pipelineTabHealth}`}>{renderTabHealthBadge(pipelineTabHealth)}</span>
                </div>
              </div>
              <p className="section-meta">{formatSectionLoadedAt(pipelineLoadedAt)}</p>
              <p className="pipeline-next-action">Next action: {pipelineHealth.next_action}</p>

              <div className="pipeline-stage-list">
                {pipelineHealth.stages.map((stage) => (
                  <div key={stage.key} className="pipeline-stage-item">
                    <div className="pipeline-stage-topline">
                      <strong>{stage.label}</strong>
                      <span className={`pipeline-status-pill ${stage.status === "ready" ? "ok" : "warn"}`}>
                        {stage.status.toUpperCase()} {stage.current}/{stage.target}
                      </span>
                    </div>
                    <p>{stage.detail}</p>
                  </div>
                ))}
              </div>

              <div className="source-table-wrap">
                <table className="source-table">
                  <thead>
                    <tr>
                      <th>Source</th>
                      <th>Status</th>
                      <th>Rows (24h)</th>
                      <th>Total Rows</th>
                      <th>Last Seen (UTC)</th>
                      <th>Recent Range</th>
                    </tr>
                  </thead>
                  <tbody>
                    {pipelineHealth.sources.map((source) => (
                      <tr key={source.key}>
                        <td>{source.label}</td>
                        <td>
                          <span
                            className={`pipeline-status-pill ${
                              source.status === "healthy"
                                ? "ok"
                                : source.status === "aging"
                                  ? "warn"
                                  : "bad"
                            }`}
                          >
                            {source.status.toUpperCase()}
                          </span>
                        </td>
                        <td>{source.rows_24h}</td>
                        <td>{source.total_rows}</td>
                        <td>{formatIsoDate(source.last_seen)}</td>
                        <td>
                          {source.recent_min === null || source.recent_max === null
                            ? "n/a"
                            : `${source.recent_min.toFixed(2)} to ${source.recent_max.toFixed(2)}`}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </>
      )}

      {/* CONTROLS TAB */}
      {activeTab === "controls" && (
        <>
          <div className="parity-detail-card">
            <h3>Seed Bootstrap Bundle</h3>
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
            <div className="controls-row" style={{ marginTop: 12 }}>
              <button type="button" onClick={handleSeedBundle} disabled={actionState === "running"}>
                Seed Bundle
              </button>
            </div>
          </div>

          <div className="parity-detail-card">
            <h3>Update Forecast Job</h3>
            <p>Run a complete update: ingest data, train ML, write forecasts, evaluate parity.</p>
            <div className="controls-row">
              <label>
                ML Write Mode
                <select
                  value={mlWriteMode}
                  onChange={(event) => void handleMlWriteModeChange(event.target.value as MlWriteModeStatus["mode"])}
                  disabled={actionState === "running" || loadingMlWriteMode}
                >
                  <option value="deterministic">Deterministic</option>
                  <option value="shadow">Shadow</option>
                  <option value="ml">ML</option>
                </select>
              </label>
              <button type="button" onClick={handleRunUpdateJob} disabled={actionState === "running"}>
                {actionState === "running" && activeActionKind === "update" ? "Running Update Job..." : "Run Update Job"}
              </button>
            </div>
            {activeActionKind === "update" && actionMessage && (
              <p className={`inline-action-message ${actionState === "running" ? "info" : "success"}`}>{actionMessage}</p>
            )}
          </div>

          <div className="parity-detail-card">
            <h3>Parity History Filter</h3>
            <div className="controls-row">
              <label>
                Status
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
                Time Window (Hours)
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
            <div className="controls-row" style={{ marginTop: 12 }}>
              <button type="button" onClick={() => void refreshAll()} disabled={actionState === "running"}>
                Refresh All Data
              </button>
            </div>
          </div>
        </>
      )}

      {/* DISCORD TAB */}
      {activeTab === "discord" && (
        <>
          {loadingDiscord && (
            <div className="parity-detail-card">
              <p className="section-loading">Loading Discord configuration...</p>
              <div className="skeleton-stack">
                <div className="skeleton-line" />
              </div>
            </div>
          )}
          <div className={`parity-detail-card progressive-card ${loadingDiscord ? "is-loading" : "is-ready"}`}>
            <div className="chart-header">
              <h3>Discord Notifications</h3>
              <div className="header-badges">
                <span className={`pipeline-status-pill ${discordEnabled ? "ok" : "warn"}`}>
                  {discordEnabled ? "Configured" : "Not Configured"}
                </span>
                <span className={`section-health-pill ${discordTabHealth}`}>{renderTabHealthBadge(discordTabHealth)}</span>
              </div>
            </div>
            <p className="section-meta">{formatSectionLoadedAt(discordLoadedAt)}</p>
            <p>
              Configure a Discord webhook and choose which operational alerts should be sent automatically.
            </p>

            <div className="controls-row" style={{ marginTop: 12 }}>
              <label>
                Webhook URL
                <input
                  type="text"
                  placeholder="https://discord.com/api/webhooks/..."
                  value={discordWebhookUrl}
                  onChange={(event) => {
                    setDiscordWebhookUrl(event.target.value);
                    setDiscordSaveState("idle");
                  }}
                  style={{ width: "100%", marginTop: 8 }}
                />
              </label>
            </div>

            <div className="discord-toggle-list">
              <label className="discord-toggle-item">
                <input
                  type="checkbox"
                  checked={discordNotifications.update_started}
                  onChange={(event) => handleDiscordNotificationToggle("update_started", event.target.checked)}
                />
                <span>Forecast update started</span>
              </label>
              <label className="discord-toggle-item">
                <input
                  type="checkbox"
                  checked={discordNotifications.update_success}
                  onChange={(event) => handleDiscordNotificationToggle("update_success", event.target.checked)}
                />
                <span>Forecast update completed</span>
              </label>
              <label className="discord-toggle-item">
                <input
                  type="checkbox"
                  checked={discordNotifications.update_failure}
                  onChange={(event) => handleDiscordNotificationToggle("update_failure", event.target.checked)}
                />
                <span>Forecast update failed</span>
              </label>
              <label className="discord-toggle-item">
                <input
                  type="checkbox"
                  checked={discordNotifications.parity_alert}
                  onChange={(event) => handleDiscordNotificationToggle("parity_alert", event.target.checked)}
                />
                <span>ML parity drift alert</span>
              </label>
              <label className="discord-toggle-item">
                <input
                  type="checkbox"
                  checked={discordNotifications.gpu_alert}
                  onChange={(event) => handleDiscordNotificationToggle("gpu_alert", event.target.checked)}
                />
                <span>GPU fallback or incompatibility</span>
              </label>
              <label className="discord-toggle-item">
                <input
                  type="checkbox"
                  checked={discordNotifications.daily_digest}
                  onChange={(event) => handleDiscordNotificationToggle("daily_digest", event.target.checked)}
                />
                <span>Daily forecast digest</span>
              </label>
              <label className="discord-toggle-item">
                <input
                  type="checkbox"
                  checked={discordNotifications.pipeline_staleness}
                  onChange={(event) => handleDiscordNotificationToggle("pipeline_staleness", event.target.checked)}
                />
                <span>Pipeline staleness or fallback alert</span>
              </label>
            </div>

            <div className="controls-row" style={{ marginTop: 12 }}>
              <button
                type="button"
                onClick={handleSaveDiscordConfig}
                disabled={discordSaveState === "saving"}
              >
                {discordSaveState === "saving"
                  ? "Saving..."
                  : discordSaveState === "saved"
                    ? "Saved"
                    : "Save Settings"}
              </button>
              <button
                type="button"
                onClick={handleSendDiscordTest}
                disabled={discordTestState === "testing"}
              >
                {discordTestState === "testing" ? "Sending Test..." : "Send Test Notification"}
              </button>
            </div>
          </div>

          <div className="parity-detail-card">
            <h3>Live Notification Coverage</h3>
            <ul className="history-list">
              <li>Manual update started notification as soon as the job is triggered from the UI.</li>
              <li>Update completion summary with source, record count, device used, and ML drift metrics.</li>
              <li>Update failure alerts for both manual runs and scheduled auto-update runs.</li>
              <li>GPU alerts when GPU is requested but the runtime falls back to CPU.</li>
              <li>Parity drift alerts when ML candidate output exceeds notification thresholds.</li>
              <li>Daily digest sent once per UTC day with low, high, and average next-day forecast values.</li>
              <li>Pipeline staleness alerts when ingest falls back, retries heavily, or arrives short of the aligned horizon.</li>
            </ul>
          </div>

          <div className="parity-detail-card">
            <h3>Suggested Next Notifications</h3>
            <ul className="history-list">
              <li>Model confidence downgrade alerts when confidence drops from high to medium or low.</li>
              <li>Scheduled market-open summary with top 3 cheapest and most expensive forecast slots.</li>
              <li>Separate alerts for individual upstream feeds so Nordpool and weather issues are distinguishable.</li>
            </ul>
          </div>
        </>
      )}

      {/* FEED HEALTH TAB */}
      {activeTab === "feed-health" && (
        <>
          {loadingFeedHealth && (
            <div className="parity-detail-card">
              <p className="section-loading">Loading feed health by source...</p>
              <div className="skeleton-stack">
                <div className="skeleton-line long" />
                <div className="skeleton-line" />
              </div>
            </div>
          )}
          <div className={`parity-detail-card progressive-card ${loadingFeedHealth ? "is-loading" : "is-ready"}`}>
            <div className="chart-header">
              <h3>External Feed Health</h3>
              <div className="header-badges">
                <span className="pipeline-status-pill ok">Real-time Monitoring</span>
                <span className={`section-health-pill ${feedHealthTabHealth}`}>{renderTabHealthBadge(feedHealthTabHealth)}</span>
              </div>
            </div>
            <p className="section-meta">{formatSectionLoadedAt(feedHealthLoadedAt)}</p>
            <p>
              Status of all upstream data sources. Use this to identify which feed is broken when forecasts fail.
            </p>
          </div>

          {feedHealth && (
            <>
              {/* Agile Regions Group */}
              <div className="parity-detail-card">
                <h3>Agile UK Tariff (Octopus Energy)</h3>
                <div className="feed-group">
                  {Object.entries(feedHealth)
                    .filter(([id]) => id.startsWith("agile_"))
                    .map(([id, data]) => {
                      const entry = data as any;
                      const statusClass = feedStatusClass(entry.status);
                      return (
                        <div key={id} className={`feed-item feed-${statusClass}`}>
                          <div className="feed-name">{entry.name}</div>
                          <div className="feed-details">
                            <span className={`feed-status feed-status-${statusClass}`}>{feedStatusLabel(entry.status)}</span>
                            {entry.last_successful_pull && (
                              <span className="feed-timestamp">Last: {formatUpdateRelativeTime(entry.last_successful_pull, nowMs)}</span>
                            )}
                            {entry.records_received > 0 && (
                              <span className="feed-count">{entry.records_received} records</span>
                            )}
                            {entry.last_error && (
                              <span className="feed-error" title={entry.last_error}>
                                Error: {entry.last_error.substring(0, 40)}...
                              </span>
                            )}
                            {Array.isArray(entry.validation_issues) && entry.validation_issues.length > 0 && (
                              <span className="feed-quality">
                                Quality: {entry.validation_issues.slice(0, 2).join(", ")}
                              </span>
                            )}
                            <span className="feed-action">
                              <button
                                type="button"
                                onClick={() => {
                                  void handleRefreshFeedSource(id, entry.name ?? id);
                                }}
                                disabled={Boolean(refreshingFeeds[id])}
                              >
                                {refreshingFeeds[id] ? "Updating..." : "Update feed"}
                              </button>
                            </span>
                          </div>
                        </div>
                      );
                    })}
                </div>
              </div>

              {/* Day-Ahead Prices */}
              <div className="parity-detail-card">
                <h3>Day-Ahead Prices (Nordpool/N2EX)</h3>
                <div className="feed-group">
                  {Object.entries(feedHealth)
                    .filter(([id]) => id.startsWith("nordpool_"))
                    .map(([id, data]) => {
                      const entry = data as any;
                      const statusClass = feedStatusClass(entry.status);
                      return (
                        <div key={id} className={`feed-item feed-${statusClass}`}>
                          <div className="feed-name">{entry.name}</div>
                          <div className="feed-details">
                            <span className={`feed-status feed-status-${statusClass}`}>{feedStatusLabel(entry.status)}</span>
                            {entry.last_successful_pull && (
                              <span className="feed-timestamp">Last: {formatUpdateRelativeTime(entry.last_successful_pull, nowMs)}</span>
                            )}
                            {entry.records_received > 0 && (
                              <span className="feed-count">{entry.records_received} records</span>
                            )}
                            {entry.last_error && (
                              <span className="feed-error" title={entry.last_error}>
                                Error: {entry.last_error.substring(0, 40)}...
                              </span>
                            )}
                            {Array.isArray(entry.validation_issues) && entry.validation_issues.length > 0 && (
                              <span className="feed-quality">
                                Quality: {entry.validation_issues.slice(0, 2).join(", ")}
                              </span>
                            )}
                            <span className="feed-action">
                              <button
                                type="button"
                                onClick={() => {
                                  void handleRefreshFeedSource(id, entry.name ?? id);
                                }}
                                disabled={Boolean(refreshingFeeds[id])}
                              >
                                {refreshingFeeds[id] ? "Updating..." : "Update feed"}
                              </button>
                            </span>
                          </div>
                        </div>
                      );
                    })}
                </div>
              </div>

              {/* Weather */}
              <div className="parity-detail-card">
                <h3>Weather Data (Open-Meteo)</h3>
                <div className="feed-group">
                  {Object.entries(feedHealth)
                    .filter(([id]) => id.startsWith("weather_"))
                    .map(([id, data]) => {
                      const entry = data as any;
                      const statusClass = feedStatusClass(entry.status);
                      return (
                        <div key={id} className={`feed-item feed-${statusClass}`}>
                          <div className="feed-name">{entry.name}</div>
                          <div className="feed-details">
                            <span className={`feed-status feed-status-${statusClass}`}>{feedStatusLabel(entry.status)}</span>
                            {entry.last_successful_pull && (
                              <span className="feed-timestamp">Last: {formatUpdateRelativeTime(entry.last_successful_pull, nowMs)}</span>
                            )}
                            {entry.records_received > 0 && (
                              <span className="feed-count">{entry.records_received} records</span>
                            )}
                            {entry.last_error && (
                              <span className="feed-error" title={entry.last_error}>
                                Error: {entry.last_error.substring(0, 40)}...
                              </span>
                            )}
                            {Array.isArray(entry.validation_issues) && entry.validation_issues.length > 0 && (
                              <span className="feed-quality">
                                Quality: {entry.validation_issues.slice(0, 2).join(", ")}
                              </span>
                            )}
                            <span className="feed-action">
                              <button
                                type="button"
                                onClick={() => {
                                  void handleRefreshFeedSource(id, entry.name ?? id);
                                }}
                                disabled={Boolean(refreshingFeeds[id])}
                              >
                                {refreshingFeeds[id] ? "Updating..." : "Update feed"}
                              </button>
                            </span>
                          </div>
                        </div>
                      );
                    })}
                </div>
              </div>

              {/* NESO Grid Data */}
              <div className="parity-detail-card">
                <h3>NESO Grid Data (UK)</h3>
                <div className="feed-group">
                  {Object.entries(feedHealth)
                    .filter(([id]) => id.startsWith("neso_"))
                    .map(([id, data]) => {
                      const entry = data as any;
                      const statusClass = feedStatusClass(entry.status);
                      return (
                        <div key={id} className={`feed-item feed-${statusClass}`}>
                          <div className="feed-name">{entry.name}</div>
                          <div className="feed-details">
                            <span className={`feed-status feed-status-${statusClass}`}>{feedStatusLabel(entry.status)}</span>
                            {entry.last_successful_pull && (
                              <span className="feed-timestamp">Last: {formatUpdateRelativeTime(entry.last_successful_pull, nowMs)}</span>
                            )}
                            {entry.records_received > 0 && (
                              <span className="feed-count">{entry.records_received} records</span>
                            )}
                            {entry.last_error && (
                              <span className="feed-error" title={entry.last_error}>
                                Error: {entry.last_error.substring(0, 40)}...
                              </span>
                            )}
                            {Array.isArray(entry.validation_issues) && entry.validation_issues.length > 0 && (
                              <span className="feed-quality">
                                Quality: {entry.validation_issues.slice(0, 2).join(", ")}
                              </span>
                            )}
                            <span className="feed-action">
                              <button
                                type="button"
                                onClick={() => {
                                  void handleRefreshFeedSource(id, entry.name ?? id);
                                }}
                                disabled={Boolean(refreshingFeeds[id])}
                              >
                                {refreshingFeeds[id] ? "Updating..." : "Update feed"}
                              </button>
                            </span>
                          </div>
                        </div>
                      );
                    })}
                </div>
              </div>

              {/* Elexon BMRS Fallback */}
              <div className="parity-detail-card">
                <h3>Elexon BMRS Fallback Sources</h3>
                <div className="feed-group">
                  {Object.entries(feedHealth)
                    .filter(([id]) => id.startsWith("elexon_"))
                    .map(([id, data]) => {
                      const entry = data as any;
                      const statusClass = feedStatusClass(entry.status);
                      return (
                        <div key={id} className={`feed-item feed-${statusClass}`}>
                          <div className="feed-name">{entry.name}</div>
                          <div className="feed-details">
                            <span className={`feed-status feed-status-${statusClass}`}>{feedStatusLabel(entry.status)}</span>
                            {entry.last_successful_pull && (
                              <span className="feed-timestamp">Last: {formatUpdateRelativeTime(entry.last_successful_pull, nowMs)}</span>
                            )}
                            {entry.records_received > 0 && (
                              <span className="feed-count">{entry.records_received} records</span>
                            )}
                            {entry.last_error && (
                              <span className="feed-error" title={entry.last_error}>
                                Error: {entry.last_error.substring(0, 40)}...
                              </span>
                            )}
                            {Array.isArray(entry.validation_issues) && entry.validation_issues.length > 0 && (
                              <span className="feed-quality">
                                Quality: {entry.validation_issues.slice(0, 2).join(", ")}
                              </span>
                            )}
                            <span className="feed-action">
                              <button
                                type="button"
                                onClick={() => {
                                  void handleRefreshFeedSource(id, entry.name ?? id);
                                }}
                                disabled={Boolean(refreshingFeeds[id])}
                              >
                                {refreshingFeeds[id] ? "Updating..." : "Update feed"}
                              </button>
                            </span>
                          </div>
                        </div>
                      );
                    })}
                </div>
              </div>
            </>
          )}
        </>
      )}
    </section>
  );
}
