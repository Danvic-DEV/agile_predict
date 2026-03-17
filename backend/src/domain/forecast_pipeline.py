from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import time

import pandas as pd

from src.ml.features import add_time_features
from src.ml.ingest import fetch_day_ahead_prices
from src.ml.transforms import day_ahead_to_agile


@dataclass
class ForecastRunResult:
    records_written: int
    forecast_name: str
    source: str | None = None
    day_ahead_points: int | None = None


@dataclass(frozen=True)
class ForecastPipelineOutput:
    day_ahead_values: tuple[float, ...]
    source: str
    agile_preview_mean: float
    ingest_error: str | None = None
    raw_points: int = 0
    aligned_points: int = 0
    interpolated_points: int = 0
    retries_used: int = 0


def _fallback_day_ahead_series(points: int = 48) -> pd.Series:
    idx = pd.date_range(datetime.now(timezone.utc), periods=points, freq="30min", tz="UTC")
    values = [80.0 + 0.35 * (i % 16) for i in range(points)]
    return pd.Series(index=idx, data=values, dtype=float, name="day_ahead")


def _ingest_stage(
    now: datetime | None = None,
    fallback_points: int = 48,
    max_attempts: int = 3,
    retry_backoff_seconds: float = 1.0,
) -> tuple[pd.Series, str, str | None, int, int]:
    last_error: str | None = None
    for attempt in range(max_attempts):
        try:
            day_ahead_map = fetch_day_ahead_prices(now=now)
            if not day_ahead_map:
                raise ValueError("empty day-ahead payload")

            sorted_points = sorted(day_ahead_map.items(), key=lambda item: item[0])
            idx = pd.DatetimeIndex([dt for dt, _ in sorted_points])
            if idx.tz is None:
                idx = idx.tz_localize("UTC")
            else:
                idx = idx.tz_convert("UTC")

            series = pd.Series(index=idx, data=[float(v) for _, v in sorted_points], dtype=float, name="day_ahead")
            return series, "nordpool", None, attempt, len(series)
        except Exception as exc:
            last_error = str(exc)
            if attempt < max_attempts - 1:
                time.sleep(retry_backoff_seconds * (attempt + 1))

    fallback = _fallback_day_ahead_series(points=fallback_points)
    return fallback, "fallback", last_error, max_attempts - 1, len(fallback)


def _quality_stage(day_ahead: pd.Series, max_points: int = 48) -> tuple[pd.Series, int]:
    if day_ahead.empty:
        fallback = _fallback_day_ahead_series(points=max_points)
        return fallback, 0

    series = day_ahead.sort_index()
    series = series.groupby(series.index).mean()

    points = max_points
    start = pd.Timestamp(series.index[0]).tz_convert("UTC") if series.index.tz is not None else pd.Timestamp(series.index[0], tz="UTC")
    target_idx = pd.date_range(start=start, periods=points, freq="30min", tz="UTC")
    aligned = series.reindex(target_idx)
    missing_before_fill = int(aligned.isna().sum())
    aligned = aligned.interpolate(method="time").ffill().bfill()
    aligned.name = "day_ahead"
    return aligned.astype(float), missing_before_fill


def _feature_stage(day_ahead: pd.Series) -> pd.DataFrame:
    frame = pd.DataFrame(index=day_ahead.index, data={"day_ahead": day_ahead.values})
    return add_time_features(frame)


def _infer_stage(featured: pd.DataFrame) -> tuple[tuple[float, ...], float]:
    day_ahead = featured["day_ahead"].astype(float)
    agile_preview = day_ahead_to_agile(day_ahead, region="G")
    return tuple(float(v) for v in day_ahead.round(4).tolist()), float(agile_preview.mean())


def run_forecast_pipeline(now: datetime | None = None, fallback_points: int = 48) -> ForecastPipelineOutput:
    ingested, source, ingest_error, retries_used, raw_points = _ingest_stage(now=now, fallback_points=fallback_points)
    quality_checked, interpolated_points = _quality_stage(ingested, max_points=fallback_points)
    featured = _feature_stage(quality_checked)
    day_ahead_values, agile_preview_mean = _infer_stage(featured)

    return ForecastPipelineOutput(
        day_ahead_values=day_ahead_values,
        source=source,
        agile_preview_mean=agile_preview_mean,
        ingest_error=ingest_error,
        raw_points=raw_points,
        aligned_points=len(day_ahead_values),
        interpolated_points=interpolated_points,
        retries_used=retries_used,
    )
