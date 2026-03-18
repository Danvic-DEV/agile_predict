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

log = logging.getLogger(__name__)

_UA = "Mozilla/5.0 (compatible; agile-predict)"
_TIMEOUT = 30


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


def _fetch_neso_demand(start_date: str) -> pd.Series:
    """INDO dataset: actual demand (last 28d via Elexon, falls back to NESO)."""
    try:
        url = "https://data.elexon.co.uk/bmrs/api/v1/datasets/INDO"
        params = {
            "publishDateTimeFrom": (pd.Timestamp.now() - pd.Timedelta("27D")).strftime("%Y-%m-%d"),
            "publishDateTimeTo": (pd.Timestamp.now() + pd.Timedelta("1D")).strftime("%Y-%m-%d"),
            "format": "json",
        }
        data = _retry(lambda: _get_json(url, params))
        df = pd.DataFrame(data["data"])
        df.index = pd.to_datetime(df["startTime"], utc=True)
        df = df[["demand"]].sort_index().astype(float)
        df = df.resample("30min").mean().interpolate()
        return df["demand"]
    except Exception as exc:  # noqa: BLE001
        log.warning("Elexon INDO failed, trying NESO: %s", exc)

    # Fallback: NESO settlement-period demand datasets
    for rid in (
        "bf5ab335-9b40-4ea4-b93a-ab4af7bce003",
        "f6d02c0f-957b-48cb-82ee-09003f2ba759",
    ):
        try:
            df = _neso_sql(rid, f'"SETTLEMENT_DATE" >= \'{start_date}T00:00:00Z\'')
            if df.empty:
                continue
            df.index = pd.to_datetime(df["SETTLEMENT_DATE"], utc=True) + (
                df["SETTLEMENT_PERIOD"].astype(int) - 1
            ) * pd.Timedelta("30min")
            series = df["ND"].astype(float).sort_index()
            series = series.resample("30min").mean().interpolate()
            return series
        except Exception as exc2:  # noqa: BLE001
            log.warning("NESO demand resource %s failed: %s", rid, exc2)

    return pd.Series(dtype=float)


def _fetch_neso_bm_wind(start_date: str) -> pd.Series:
    """BM wind incentive forecast from NESO."""
    try:
        df = _neso_sql(
            "7524ec65-f782-4258-aaf8-5b926c17b966",
            f'"Datetime_GMT" >= \'{start_date}T00:00:00Z\'',
            limit=40000,
        )
        df.index = pd.to_datetime(df["Datetime_GMT"], utc=True)
        series = df["Incentive_forecast"].astype(float).sort_index()
        series = series.resample("30min").mean().interpolate()
        return series
    except Exception as exc:  # noqa: BLE001
        log.warning("NESO BM wind failed: %s", exc)
        return pd.Series(dtype=float)


def _fetch_neso_solar_wind(start_date: str) -> pd.DataFrame:
    """Embedded solar and wind from NESO."""
    try:
        df = _neso_sql(
            "f93d1835-75bc-43e5-84ad-12472b180a98",
            f'"DATETIME" >= \'{start_date}\'',
            limit=20000,
        )
        df.index = pd.to_datetime(df["DATETIME"], utc=True)
        out = df[["SOLAR", "WIND"]].rename(columns={"SOLAR": "solar", "WIND": "total_wind"}).astype(float).sort_index()
        out = out.resample("30min").mean().interpolate()
        return out
    except Exception as exc:  # noqa: BLE001
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
        return df
    except Exception as exc:  # noqa: BLE001
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
        params = {
            "latitude": 54.0,
            "longitude": 2.3,
            "start_date": start_date,
            "end_date": end_date,
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
        forecast_start = (pd.Timestamp.now(tz="UTC").normalize() - pd.Timedelta("5D")).strftime("%Y-%m-%d")
        forecast_end = pd.Timestamp.now(tz="UTC").normalize().strftime("%Y-%m-%d")
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

    return merged[["temp_2m", "wind_10m", "rad"]].sort_index()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_grid_weather_features(
    lookback_days: int = 62,
    now: datetime | None = None,
) -> pd.DataFrame:
    """
    Fetch real grid + weather features for the past `lookback_days`.

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
    end_date = ref_ts.normalize().strftime("%Y-%m-%d")

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

    # Ensure required columns exist (fill with column mean if partial)
    required = ["bm_wind", "solar", "emb_wind", "demand", "temp_2m", "wind_10m", "rad"]
    for col in required:
        if col not in combined.columns:
            log.warning("Feature column %s not available; filling with 0", col)
            combined[col] = 0.0

    combined = combined[required]
    combined = combined.ffill().bfill().astype(float)

    # Drop rows where more than half the required columns are still NaN
    combined = combined.dropna(thresh=len(required) // 2 + 1)

    if combined.empty:
        raise RuntimeError("Grid/weather features empty after alignment")

    return combined
