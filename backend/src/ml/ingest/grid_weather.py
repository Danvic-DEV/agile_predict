"""Fetch real grid and weather feature data from public APIs.

Sources:
- NESO open data portal (demand, embedded solar/wind, BM wind)
- Elexon BMRS (NDF demand forecast)
- Open-Meteo archive + forecast (temperature, wind speed, radiation)

All calls are unauthenticated public endpoints.  Returns a DataFrame indexed
on UTC DatetimeIndex with 30-minute frequency containing:
    bm_wind, solar, emb_wind, demand, temp_2m, wind_10m, rad
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from urllib.parse import urlencode
from urllib.request import urlopen, Request

import pandas as pd
from src.core.feed_health import record_feed_error, record_feed_success

log = logging.getLogger(__name__)

_UA = "Mozilla/5.0 (compatible; agile-predict)"
_TIMEOUT = 30
_ELEXON_INDO_RETENTION = pd.Timedelta("30D")
_NESO_HISTORIC_DEMAND_RESOURCES = {
    2023: "bf5ab335-9b40-4ea4-b93a-ab4af7bce003",
    2024: "f6d02c0f-957b-48cb-82ee-09003f2ba759",
    2025: "b2bde559-3455-4021-b179-dfe60c0337b0",
    2026: "8a4a771c-3929-4e56-93ad-cdf13219dea5",
}


def _validate_series(
    series: pd.Series,
    *,
    min_rows: int,
    min_value: float | None = None,
    max_value: float | None = None,
) -> tuple[str, list[str], dict]:
    issues: list[str] = []
    if series.empty:
        issues.append("empty_series")
    if len(series) < min_rows:
        issues.append(f"low_point_count={len(series)}")

    null_ratio = float(series.isna().mean()) if len(series) else 1.0
    if null_ratio > 0.05:
        issues.append(f"high_null_ratio={null_ratio:.3f}")

    duplicate_ratio = (
        float(series.index.duplicated().sum()) / float(len(series)) if len(series) else 0.0
    )
    if duplicate_ratio > 0.05:
        issues.append(f"high_duplicate_ratio={duplicate_ratio:.3f}")

    non_na = series.dropna()
    if not non_na.empty:
        min_seen = float(non_na.min())
        max_seen = float(non_na.max())
        if min_value is not None and min_seen < min_value:
            issues.append(f"below_expected_min={min_seen:.3f}")
        if max_value is not None and max_seen > max_value:
            issues.append(f"above_expected_max={max_seen:.3f}")
        if int(non_na.nunique()) <= 2:
            issues.append("near_flat_series")
    else:
        min_seen = None
        max_seen = None

    status = "warn" if issues else "pass"
    metrics = {
        "min": min_seen,
        "max": max_seen,
        "null_ratio": null_ratio,
        "duplicate_ratio": duplicate_ratio,
    }
    return status, issues, metrics


def _get_json(url: str, params: dict | None = None, timeout: int = _TIMEOUT) -> dict | list:
    if params:
        url = f"{url}?{urlencode(params)}"
    req = Request(url, headers={"User-Agent": _UA})
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _retry(fn, retries: int = 3, backoff: float = 2.0):
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt < retries - 1:
                time.sleep(backoff * (attempt + 1))
    raise last_exc  # type: ignore[misc]


# ---------------------------------------------------------------------------
# NESO helpers
# ---------------------------------------------------------------------------

def _neso_sql(resource_id: str, where_clause: str, limit: int = 40000) -> pd.DataFrame:
    sql = (
        f'SELECT * FROM "{resource_id}" WHERE {where_clause} ORDER BY "_id" ASC LIMIT {limit}'
    )
    url = "https://api.neso.energy/api/3/action/datastore_search_sql"
    data = _retry(lambda: _get_json(url, {"sql": sql}))
    return pd.DataFrame(data["result"]["records"])


def _fetch_elexon_ndf_forecast() -> pd.Series:
    """Latest Elexon NDF forecast series."""
    try:
        data = _retry(lambda: _get_json("https://data.elexon.co.uk/bmrs/api/v1/datasets/NDF", {"format": "json"}))
        df = pd.DataFrame(data.get("data", []))
        if df.empty:
            return pd.Series(dtype=float)

        df["publishTime"] = pd.to_datetime(df["publishTime"], utc=True)
        df["startTime"] = pd.to_datetime(df["startTime"], utc=True)
        latest_publish = df["publishTime"].max()
        df = df[df["publishTime"] == latest_publish].copy()
        series = df.set_index("startTime")["demand"].astype(float).sort_index()
        series = series.resample("30min").mean().interpolate()
        status, issues, metrics = _validate_series(
            series, min_rows=24, min_value=0.0, max_value=100000.0
        )
        record_feed_success(
            "elexon_ndf",
            records_received=len(series),
            validation_status=status,
            validation_issues=issues,
            validation_metrics=metrics,
        )
        return series
    except Exception as exc:  # noqa: BLE001
        record_feed_error("elexon_ndf", str(exc))
        log.warning("Elexon NDF failed: %s", exc)
        return pd.Series(dtype=float)


def _fetch_neso_historic_demand(start_dt: pd.Timestamp) -> pd.Series:
    """Historical demand from NESO yearly datasets."""
    frames: list[pd.Series] = []
    current_year = datetime.now(timezone.utc).year

    for year in sorted(_NESO_HISTORIC_DEMAND_RESOURCES):
        if year < start_dt.year or year > current_year:
            continue

        resource_id = _NESO_HISTORIC_DEMAND_RESOURCES[year]
        year_start = max(start_dt, pd.Timestamp(f"{year}-01-01", tz="UTC"))
        year_end = pd.Timestamp(f"{year + 1}-01-01", tz="UTC")
        where_clause = (
            f'"SETTLEMENT_DATE" >= \'{year_start.strftime("%Y-%m-%d")}\''
            f' AND "SETTLEMENT_DATE" < \'{year_end.strftime("%Y-%m-%d")}\''
        )

        try:
            df = _neso_sql(resource_id, where_clause)
        except Exception as exc:  # noqa: BLE001
            log.warning("NESO demand resource %s failed: %s", resource_id, exc)
            continue

        if df.empty:
            continue

        df.index = pd.to_datetime(df["SETTLEMENT_DATE"], utc=True) + (
            df["SETTLEMENT_PERIOD"].astype(int) - 1
        ) * pd.Timedelta("30min")
        frames.append(df["ND"].astype(float).sort_index())

    if not frames:
        return pd.Series(dtype=float)

    series = pd.concat(frames).sort_index()
    series = series[~series.index.duplicated(keep="last")]
    return series.resample("30min").mean().interpolate()


def _fetch_neso_demand(start_date: str) -> pd.Series:
    """Demand series using INDO actuals plus NDF future forecast when available."""
    try:
        requested_start = pd.Timestamp(start_date, tz="UTC")
    except Exception:
        requested_start = pd.Timestamp.now(tz="UTC") - _ELEXON_INDO_RETENTION

    actual_series = pd.Series(dtype=float)
    try:
        url = "https://data.elexon.co.uk/bmrs/api/v1/datasets/INDO"
        recent_cutoff = pd.Timestamp.now(tz="UTC") - _ELEXON_INDO_RETENTION
        start_dt = max(requested_start, recent_cutoff)

        params = {
            "startTimeFrom": start_dt.strftime("%Y-%m-%dT%H:%MZ"),
            "startTimeTo": (pd.Timestamp.now(tz="UTC") + pd.Timedelta("1D")).strftime("%Y-%m-%dT%H:%MZ"),
            "format": "json",
        }
        data = _retry(lambda: _get_json(url, params))
        df = pd.DataFrame(data["data"])
        df.index = pd.to_datetime(df["startTime"], utc=True)
        df = df[["demand"]].sort_index().astype(float)
        df = df.resample("30min").mean().interpolate()
        status, issues, metrics = _validate_series(df["demand"], min_rows=24, min_value=0.0, max_value=100000.0)
        record_feed_success("elexon_indo", records_received=len(df))
        record_feed_success(
            "neso_demand",
            records_received=len(df),
            validation_status=status,
            validation_issues=issues,
            validation_metrics=metrics,
        )
        actual_series = df["demand"]
    except Exception as exc:  # noqa: BLE001
        record_feed_error("elexon_indo", str(exc))
        log.warning("Elexon INDO failed, trying NESO: %s", exc)

    forecast_series = _fetch_elexon_ndf_forecast()

    historic_series = _fetch_neso_historic_demand(requested_start)

    combined = actual_series.combine_first(historic_series)
    combined = combined.combine_first(forecast_series).sort_index()

    if not combined.empty:
        status, issues, metrics = _validate_series(combined, min_rows=24, min_value=0.0, max_value=100000.0)
        record_feed_success(
            "neso_demand",
            records_received=len(combined),
            validation_status=status,
            validation_issues=issues,
            validation_metrics=metrics,
        )
        return combined

    record_feed_error("neso_demand", "all NESO/Elexon demand sources failed")
    return pd.Series(dtype=float)


def _fetch_neso_bm_wind(start_date: str) -> pd.Series:
    """BM wind incentive forecast from NESO."""
    try:
        # Normalize start_date to ensure proper format
        try:
            start_dt = pd.Timestamp(start_date, tz='UTC')
            start_str = start_dt.strftime("%Y-%m-%d")
        except Exception:
            start_str = start_date
        
        df = _neso_sql(
            "7524ec65-f782-4258-aaf8-5b926c17b966",
            f'"Datetime_GMT" >= \'{start_str}T00:00:00Z\'',
            limit=40000,
        )
        df.index = pd.to_datetime(df["Datetime_GMT"], utc=True)
        series = df["Incentive_forecast"].astype(float).sort_index()
        series = series.resample("30min").mean().interpolate()
        status, issues, metrics = _validate_series(series, min_rows=24, min_value=0.0, max_value=30000.0)
        record_feed_success(
            "neso_bm_wind",
            records_received=len(series),
            validation_status=status,
            validation_issues=issues,
            validation_metrics=metrics,
        )
        return series
    except Exception as exc:  # noqa: BLE001
        record_feed_error("neso_bm_wind", str(exc))
        log.warning("NESO BM wind failed: %s", exc)
        return pd.Series(dtype=float)


def _fetch_neso_future_bm_wind() -> pd.Series:
    """Future BM wind forecast used by main get_latest_forecast()."""
    try:
        url = "https://api.neso.energy/api/3/action/datastore_search"
        params = {
            "resource_id": "93c3048e-1dab-4057-a2a9-417540583929",
            "limit": 1000,
        }
        data = _retry(lambda: _get_json(url, params))
        df = pd.DataFrame(data["result"]["records"])
        df.index = pd.to_datetime(df["Datetime"], utc=True)
        series = df["Wind_Forecast"].astype(float).sort_index()
        series = series.resample("30min").mean().interpolate()
        status, issues, metrics = _validate_series(series, min_rows=24, min_value=0.0, max_value=30000.0)
        record_feed_success(
            "neso_bm_wind",
            records_received=len(series),
            validation_status=status,
            validation_issues=issues,
            validation_metrics=metrics,
        )
        return series
    except Exception as exc:  # noqa: BLE001
        record_feed_error("neso_bm_wind", str(exc))
        log.warning("NESO future BM wind failed: %s", exc)
        return pd.Series(dtype=float)


def _fetch_neso_da_wind_forecast() -> pd.Series:
    """NESO incentive day-ahead wind forecast used to override BM wind."""
    try:
        url = "https://api.neso.energy/api/3/action/datastore_search"
        params = {
            "resource_id": "b2f03146-f05d-4824-a663-3a4f36090c71",
            "limit": 1000,
        }
        data = _retry(lambda: _get_json(url, params))
        df = pd.DataFrame(data["result"]["records"])
        df.index = pd.to_datetime(df["Datetime_GMT"], utc=True)
        series = df["Incentive_forecast"].astype(float).sort_index()
        return series.resample("30min").mean().interpolate()
    except Exception as exc:  # noqa: BLE001
        log.warning("NESO day-ahead wind failed: %s", exc)
        return pd.Series(dtype=float)


def _fetch_neso_national_demand_forecast() -> pd.Series:
    """NESO national demand forecast used by main get_latest_forecast()."""
    try:
        url = "https://api.neso.energy/api/3/action/datastore_search"
        params = {
            "resource_id": "7c0411cd-2714-4bb5-a408-adb065edf34d",
            "limit": 5000,
        }
        data = _retry(lambda: _get_json(url, params))
        df = pd.DataFrame(data["result"]["records"])
        df.index = pd.to_datetime(df["GDATETIME"], utc=True)
        series = df["NATIONALDEMAND"].astype(float).sort_index()
        series = series.resample("30min").mean().interpolate()
        status, issues, metrics = _validate_series(series, min_rows=24, min_value=0.0, max_value=100000.0)
        record_feed_success(
            "neso_demand",
            records_received=len(series),
            validation_status=status,
            validation_issues=issues,
            validation_metrics=metrics,
        )
        return series
    except Exception as exc:  # noqa: BLE001
        record_feed_error("neso_demand", str(exc))
        log.warning("NESO national demand forecast failed: %s", exc)
        return pd.Series(dtype=float)


def _fetch_neso_solar_wind(start_date: str) -> pd.DataFrame:
    """Embedded solar and wind from NESO."""
    try:
        # Normalize start_date to ensure proper format
        try:
            start_dt = pd.Timestamp(start_date, tz='UTC')
            start_str = start_dt.strftime("%Y-%m-%d")
        except Exception:
            start_str = start_date
        
        df = _neso_sql(
            "f93d1835-75bc-43e5-84ad-12472b180a98",
            f'"DATETIME" >= \'{start_str}\'',
            limit=20000,
        )
        df.index = pd.to_datetime(df["DATETIME"], utc=True)
        out = df[["SOLAR", "WIND"]].rename(columns={"SOLAR": "solar", "WIND": "total_wind"}).astype(float).sort_index()
        out = out.resample("30min").mean().interpolate()
        status_solar, issues_solar, metrics_solar = _validate_series(
            out["solar"], min_rows=24, min_value=0.0, max_value=30000.0
        )
        status_wind, issues_wind, metrics_wind = _validate_series(
            out["total_wind"], min_rows=24, min_value=0.0, max_value=40000.0
        )
        issues = issues_solar + issues_wind
        status = "warn" if (status_solar == "warn" or status_wind == "warn") else "pass"
        record_feed_success(
            "neso_solar_wind",
            records_received=len(out),
            validation_status=status,
            validation_issues=issues,
            validation_metrics={"solar": metrics_solar, "total_wind": metrics_wind},
        )
        return out
    except Exception as exc:  # noqa: BLE001
        record_feed_error("neso_solar_wind", str(exc))
        log.warning("NESO solar/wind failed: %s", exc)
        return pd.DataFrame(columns=["solar", "total_wind"])


def _fetch_neso_embedded(start_date: str) -> pd.DataFrame:
    """Embedded solar + embedded wind forecast from NESO (higher fidelity)."""
    try:
        url = "https://api.neso.energy/api/3/action/datastore_search"
        params = {
            "resource_id": "db6c038f-98af-4570-ab60-24d71ebd0ae5",
            "limit": 1000,
        }
        data = _retry(lambda: _get_json(url, params))
        df = pd.DataFrame(data["result"]["records"])
        # DATE_GMT + TIME_GMT columns
        df.index = pd.to_datetime(df["DATE_GMT"].str[:10] + " " + df["TIME_GMT"].str[:5], utc=True)
        df = df[["EMBEDDED_SOLAR_FORECAST", "EMBEDDED_WIND_FORECAST"]].rename(
            columns={"EMBEDDED_SOLAR_FORECAST": "solar", "EMBEDDED_WIND_FORECAST": "emb_wind"}
        ).astype(float).sort_index()
        df = df.resample("30min").mean().interpolate()
        status_solar, issues_solar, metrics_solar = _validate_series(
            df["solar"], min_rows=24, min_value=0.0, max_value=30000.0
        )
        status_wind, issues_wind, metrics_wind = _validate_series(
            df["emb_wind"], min_rows=24, min_value=0.0, max_value=40000.0
        )
        issues = issues_solar + issues_wind
        status = "warn" if (status_solar == "warn" or status_wind == "warn") else "pass"
        record_feed_success(
            "neso_embedded_solar_wind",
            records_received=len(df),
            validation_status=status,
            validation_issues=issues,
            validation_metrics={"solar": metrics_solar, "emb_wind": metrics_wind},
        )
        return df
    except Exception as exc:  # noqa: BLE001
        record_feed_error("neso_embedded_solar_wind", str(exc))
        log.warning("NESO embedded solar/wind failed: %s", exc)
        return pd.DataFrame(columns=["solar", "emb_wind"])


def _fetch_open_meteo(start_date: str, end_date: str) -> pd.DataFrame:
    """
    Fetch historical weather from Open-Meteo archive, with forecast data
    patched in for the last few days where archive may lag.
    """
    cols = ["temperature_2m", "wind_speed_10m", "direct_radiation"]
    rename = {"temperature_2m": "temp_2m", "wind_speed_10m": "wind_10m", "direct_radiation": "rad"}

    frames: list[pd.DataFrame] = []

    # Archive (historical, complete)
    try:
        archive_start = pd.Timestamp(start_date, tz="UTC").normalize()
        archive_end = min(pd.Timestamp(end_date, tz="UTC").normalize(), pd.Timestamp.now(tz="UTC").normalize())
        if archive_start <= archive_end:
            params = {
                "latitude": 54.0,
                "longitude": 2.3,
                "start_date": archive_start.strftime("%Y-%m-%d"),
                "end_date": archive_end.strftime("%Y-%m-%d"),
                "hourly": ",".join(cols),
            }
            data = _retry(lambda: _get_json("https://archive-api.open-meteo.com/v1/archive", params))
            hourly = data["hourly"]
            df = pd.DataFrame(hourly)
            df.index = pd.to_datetime(df["time"], utc=True)
            df = df[cols].rename(columns=rename).astype(float).sort_index()
            df = df.resample("30min").interpolate()
            frames.append(df)
    except Exception as exc:  # noqa: BLE001
        log.warning("Open-Meteo archive failed: %s", exc)

    # Forecast (current + next 14 days), fills gaps where archive lags
    try:
        # Request forecast data through the full caller-provided horizon.
        recent_start = (pd.Timestamp.now(tz="UTC").normalize() - pd.Timedelta("5D")).strftime("%Y-%m-%d")
        forecast_start = max(start_date, recent_start)
        forecast_end = end_date
        params_f = {
            "latitude": 54.0,
            "longitude": 2.3,
            "start_date": forecast_start,
            "end_date": forecast_end,
            "hourly": ",".join(cols),
        }
        data_f = _retry(lambda: _get_json("https://api.open-meteo.com/v1/forecast", params_f))
        hourly_f = data_f["hourly"]
        df_f = pd.DataFrame(hourly_f)
        df_f.index = pd.to_datetime(df_f["time"], utc=True)
        df_f = df_f[cols].rename(columns={"temperature_2m": "temp_2m_f", "wind_speed_10m": "wind_10m_f", "direct_radiation": "rad_f"}).astype(float).sort_index()
        df_f = df_f.resample("30min").interpolate()
        frames.append(df_f)
    except Exception as exc:  # noqa: BLE001
        log.warning("Open-Meteo forecast failed: %s", exc)

    if not frames:
        record_feed_error("weather_open_meteo", "archive and forecast weather sources failed")
        return pd.DataFrame(columns=["temp_2m", "wind_10m", "rad"])

    merged = pd.concat(frames, axis=1).sort_index()
    for col in ["temp_2m", "wind_10m", "rad"]:
        forecast_col = f"{col}_f"
        if col not in merged.columns:
            merged[col] = merged.get(forecast_col, pd.Series(dtype=float))
        elif forecast_col in merged.columns:
            merged[col] = merged[col].fillna(merged[forecast_col])

    for fc in ["temp_2m_f", "wind_10m_f", "rad_f"]:
        if fc in merged.columns:
            merged = merged.drop(columns=[fc])

    output = merged[["temp_2m", "wind_10m", "rad"]].sort_index()
    status_t, issues_t, metrics_t = _validate_series(output["temp_2m"], min_rows=24, min_value=-40.0, max_value=50.0)
    status_w, issues_w, metrics_w = _validate_series(output["wind_10m"], min_rows=24, min_value=0.0, max_value=80.0)
    status_r, issues_r, metrics_r = _validate_series(output["rad"], min_rows=24, min_value=0.0, max_value=1400.0)
    all_issues = issues_t + issues_w + issues_r
    record_feed_success(
        "weather_open_meteo",
        records_received=len(output),
        validation_status="warn" if (status_t == "warn" or status_w == "warn" or status_r == "warn") else "pass",
        validation_issues=all_issues,
        validation_metrics={"temp_2m": metrics_t, "wind_10m": metrics_w, "rad": metrics_r},
    )
    return output


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_grid_weather_features(
    lookback_days: int = 62,
    forecast_days: int = 0,
    now: datetime | None = None,
) -> pd.DataFrame:
    """
    Fetch real grid + weather features for the past `lookback_days` and the
    next `forecast_days`.

    Returns a DataFrame with UTC DatetimeIndex (30min), columns:
        bm_wind, solar, emb_wind, demand, temp_2m, wind_10m, rad

    Raises RuntimeError if no usable data could be fetched for the required
    columns.  Individual source failures are logged as warnings and the
    remaining sources are used.
    """
    ref = now or datetime.now(timezone.utc)
    # pd.Timestamp(ref, tz=...) raises if ref already carries tzinfo — normalise safely
    ref_ts = pd.Timestamp(ref)
    if ref_ts.tzinfo is None:
        ref_ts = ref_ts.tz_localize("UTC")
    else:
        ref_ts = ref_ts.tz_convert("UTC")
    start_ts = ref_ts - pd.Timedelta(days=lookback_days)
    start_date = start_ts.strftime("%Y-%m-%d")
    end_date = (ref_ts.normalize() + pd.Timedelta(days=max(0, forecast_days))).strftime("%Y-%m-%d")

    log.info("Fetching grid+weather features from %s to %s", start_date, end_date)

    demand = _fetch_neso_demand(start_date)
    bm_wind = _fetch_neso_bm_wind(start_date)
    solar_wind = _fetch_neso_solar_wind(start_date)
    embedded = _fetch_neso_embedded(start_date)
    meteo = _fetch_open_meteo(start_date, end_date)

    frames: dict[str, pd.Series | pd.DataFrame] = {}
    if not demand.empty:
        frames["demand"] = demand
    if not bm_wind.empty:
        frames["bm_wind"] = bm_wind
    if not solar_wind.empty:
        frames["solar"] = solar_wind["solar"]
        frames["total_wind"] = solar_wind["total_wind"]
    if not embedded.empty:
        if "emb_wind" in embedded.columns:
            frames["emb_wind"] = embedded["emb_wind"]
        # prefer embedded solar if available
        if "solar" in embedded.columns and "solar" not in frames:
            frames["solar"] = embedded["solar"]
    if not meteo.empty:
        for col in ["temp_2m", "wind_10m", "rad"]:
            if col in meteo.columns:
                frames[col] = meteo[col]

    if not frames:
        raise RuntimeError("All grid/weather feature sources failed")

    combined = pd.concat(list(frames.values()), axis=1)
    combined.columns = list(frames.keys())
    combined = combined.sort_index()
    combined_before_fill = combined.copy()

    # Ensure required columns exist (fill with column mean if partial)
    required = ["bm_wind", "solar", "emb_wind", "demand", "temp_2m", "wind_10m", "rad"]
    for col in required:
        if col not in combined.columns:
            log.warning("Feature column %s not available; filling with 0", col)
            combined[col] = 0.0

    combined = combined[required]
    combined = combined.ffill().bfill().astype(float)

    # Do not extrapolate stale feature values indefinitely into the future.
    # Limit output to the strictest real-data coverage across available columns.
    coverage_ends = []
    for col in required:
        if col in combined_before_fill.columns:
            non_na = combined_before_fill[col].dropna()
            if not non_na.empty:
                coverage_ends.append(non_na.index.max())
    if coverage_ends:
        strict_end = min(coverage_ends)
        # Guard against pathological source skew where one column's latest
        # timestamp trails far behind "now" and would erase all forward rows.
        # In that case, keep the full frame and let downstream horizon logic
        # truncate/report insufficiency explicitly.
        if strict_end >= (ref_ts - pd.Timedelta(hours=1)):
            combined = combined.loc[:strict_end]
        else:
            log.warning(
                "Shared feature coverage ends before reference time; skipping strict truncation "
                "(strict_end=%s ref_ts=%s)",
                strict_end,
                ref_ts,
            )

    # Drop rows where more than half the required columns are still NaN
    combined = combined.dropna(thresh=len(required) // 2 + 1)

    if combined.empty:
        raise RuntimeError("Grid/weather features empty after alignment")

    return combined


def fetch_live_forecast_features(
    forecast_days: int = 14,
    now: datetime | None = None,
) -> pd.DataFrame:
    """
    Build the live future forecast feature frame to match main get_latest_forecast().

    Returns a UTC-indexed 30-minute DataFrame containing:
        emb_wind, bm_wind, solar, demand, temp_2m, wind_10m, rad
    """
    ref = now or datetime.now(timezone.utc)
    ref_ts = pd.Timestamp(ref)
    if ref_ts.tzinfo is None:
        ref_ts = ref_ts.tz_localize("UTC")
    else:
        ref_ts = ref_ts.tz_convert("UTC")

    anchor = ref_ts.floor("30min")
    start_date = anchor.normalize().strftime("%Y-%m-%d")
    end_date = (anchor.normalize() + pd.Timedelta(days=max(1, forecast_days))).strftime("%Y-%m-%d")

    frames: dict[str, pd.Series] = {}

    bm_wind = _fetch_neso_future_bm_wind()
    if not bm_wind.empty:
        frames["bm_wind"] = bm_wind

    da_wind = _fetch_neso_da_wind_forecast()
    if not da_wind.empty:
        frames["da_wind"] = da_wind

    embedded = _fetch_neso_embedded(start_date)
    if not embedded.empty:
        if "solar" in embedded.columns:
            frames["solar"] = embedded["solar"]
        if "emb_wind" in embedded.columns:
            frames["emb_wind"] = embedded["emb_wind"]

    national_demand = _fetch_neso_national_demand_forecast()
    if not national_demand.empty:
        frames["NATIONALDEMAND"] = national_demand

    ndf_demand = _fetch_elexon_ndf_forecast()
    if not ndf_demand.empty:
        frames["demand"] = ndf_demand

    meteo = _fetch_open_meteo(start_date, end_date)
    if not meteo.empty:
        for col in ["temp_2m", "wind_10m", "rad"]:
            if col in meteo.columns:
                frames[col] = meteo[col]

    if not frames:
        raise RuntimeError("all live forecast feature sources failed")

    combined = pd.concat(list(frames.values()), axis=1)
    combined.columns = list(frames.keys())
    combined = combined.sort_index()

    missing_cols: list[str] = []
    demand_cols = ["demand", "NATIONALDEMAND"]
    if all(col in combined.columns for col in demand_cols):
        combined["demand"] = combined[demand_cols].mean(axis=1)
        combined = combined.drop(columns=["NATIONALDEMAND"])
    elif "NATIONALDEMAND" not in combined.columns:
        missing_cols.append("NATIONALDEMAND")

    if "da_wind" in combined.columns:
        if "bm_wind" not in combined.columns:
            combined["bm_wind"] = combined["da_wind"]
        else:
            combined.loc[combined["da_wind"] > 0, "bm_wind"] = combined.loc[combined["da_wind"] > 0, "da_wind"]
        combined = combined.drop(columns=["da_wind"])

    required = ["emb_wind", "bm_wind", "solar", "demand", "temp_2m", "wind_10m", "rad"]
    missing_cols.extend([col for col in required if col not in combined.columns])
    if missing_cols:
        missing_text = ", ".join(sorted(dict.fromkeys(missing_cols)))
        raise RuntimeError(f"missing live forecast feature columns: {missing_text}")

    horizon_end = anchor.normalize() + pd.Timedelta(days=max(1, forecast_days + 1))
    combined = combined[required]
    combined = combined.loc[(combined.index >= anchor) & (combined.index < horizon_end)]
    combined = combined.dropna().sort_index()

    if combined.empty:
        raise RuntimeError("live forecast features empty after alignment")

    return combined.astype(float)
