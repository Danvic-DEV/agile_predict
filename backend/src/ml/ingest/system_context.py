"""Additional system-context feeds for future ML improvements.

Current scope (Wave A starter):
- Carbon intensity (GB, 30-minute cadence)
- Elexon FUELINST stream aggregate signals (fuel mix + interconnector + pumped storage)

All sources are best-effort and unauthenticated. Errors should be handled by callers
as non-fatal so the main forecast pipeline stays resilient.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from urllib.parse import urlencode
from urllib.request import Request, urlopen

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


def fetch_carbon_intensity(start_dt: datetime, end_dt: datetime) -> pd.Series:
    """Return carbon intensity series (gCO2/kWh) at 30-minute resolution."""
    try:
        start = pd.Timestamp(start_dt).tz_convert("UTC") if pd.Timestamp(start_dt).tzinfo else pd.Timestamp(start_dt, tz="UTC")
        end = pd.Timestamp(end_dt).tz_convert("UTC") if pd.Timestamp(end_dt).tzinfo else pd.Timestamp(end_dt, tz="UTC")
        start_str = start.strftime("%Y-%m-%dT%H:%MZ")
        end_str = end.strftime("%Y-%m-%dT%H:%MZ")

        url = f"https://api.carbonintensity.org.uk/intensity/{start_str}/{end_str}"
        payload = _retry(lambda: _get_json(url))
        rows = payload.get("data", []) if isinstance(payload, dict) else []
        if not rows:
            return pd.Series(dtype=float)

        index: list[pd.Timestamp] = []
        values: list[float] = []
        for row in rows:
            ts = row.get("from")
            intensity = row.get("intensity", {})
            value = intensity.get("actual") if isinstance(intensity, dict) else None
            if value is None and isinstance(intensity, dict):
                value = intensity.get("forecast")
            if ts is None or value is None:
                continue
            index.append(pd.to_datetime(ts, utc=True))
            values.append(float(value))

        if not index:
            return pd.Series(dtype=float)

        series = pd.Series(values, index=index, dtype=float).sort_index()
        return series.resample("30min").mean().interpolate()
    except Exception as exc:  # noqa: BLE001
        log.warning("Carbon intensity fetch failed: %s", exc)
        return pd.Series(dtype=float)


def fetch_fuelinst_context(start_dt: datetime, end_dt: datetime) -> pd.DataFrame:
    """Return Elexon FUELINST aggregate context signals at 30-minute resolution.

    Columns:
    - gas_mw: CCGT + OCGT
    - wind_mw: WIND
    - nuclear_mw: NUCLEAR
    - pumped_storage_mw: PS
    - interconnector_net_mw: sum(INT*)
    """
    try:
        start = pd.Timestamp(start_dt).tz_convert("UTC") if pd.Timestamp(start_dt).tzinfo else pd.Timestamp(start_dt, tz="UTC")
        end = pd.Timestamp(end_dt).tz_convert("UTC") if pd.Timestamp(end_dt).tzinfo else pd.Timestamp(end_dt, tz="UTC")

        params = {
            "publishDateTimeFrom": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "publishDateTimeTo": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        payload = _retry(
            lambda: _get_json("https://data.elexon.co.uk/bmrs/api/v1/datasets/FUELINST/stream", params)
        )

        if isinstance(payload, list):
            rows = payload
        elif isinstance(payload, dict):
            rows = payload.get("data", [])
        else:
            rows = []
        if not rows:
            return pd.DataFrame(
                columns=["gas_mw", "wind_mw", "nuclear_mw", "pumped_storage_mw", "interconnector_net_mw"]
            )

        df = pd.DataFrame(rows)
        if "startTime" not in df.columns or "fuelType" not in df.columns or "generation" not in df.columns:
            return pd.DataFrame(
                columns=["gas_mw", "wind_mw", "nuclear_mw", "pumped_storage_mw", "interconnector_net_mw"]
            )

        df["date_time"] = pd.to_datetime(df["startTime"], utc=True)
        df["fuelType"] = df["fuelType"].astype(str).str.upper()
        df["generation"] = pd.to_numeric(df["generation"], errors="coerce")
        df = df.dropna(subset=["date_time", "generation"])
        if df.empty:
            return pd.DataFrame(
                columns=["gas_mw", "wind_mw", "nuclear_mw", "pumped_storage_mw", "interconnector_net_mw"]
            )

        pivot = (
            df.groupby(["date_time", "fuelType"], as_index=False)["generation"].sum()
            .pivot(index="date_time", columns="fuelType", values="generation")
            .fillna(0.0)
            .sort_index()
        )

        int_cols = [c for c in pivot.columns if c.startswith("INT")]
        out = pd.DataFrame(index=pivot.index)
        out["gas_mw"] = pivot.get("CCGT", 0.0) + pivot.get("OCGT", 0.0)
        out["wind_mw"] = pivot.get("WIND", 0.0)
        out["nuclear_mw"] = pivot.get("NUCLEAR", 0.0)
        out["pumped_storage_mw"] = pivot.get("PS", 0.0)
        out["interconnector_net_mw"] = pivot[int_cols].sum(axis=1) if int_cols else 0.0

        out = out.resample("30min").mean().interpolate()
        return out
    except Exception as exc:  # noqa: BLE001
        log.warning("Elexon FUELINST context fetch failed: %s", exc)
        return pd.DataFrame(
            columns=["gas_mw", "wind_mw", "nuclear_mw", "pumped_storage_mw", "interconnector_net_mw"]
        )


def fetch_system_context_features(lookback_days: int = 3, now: datetime | None = None) -> pd.DataFrame:
    """Fetch merged system context features for the recent lookback window."""
    ref = now or datetime.now(timezone.utc)
    ref_ts = pd.Timestamp(ref)
    if ref_ts.tzinfo is None:
        ref_ts = ref_ts.tz_localize("UTC")
    else:
        ref_ts = ref_ts.tz_convert("UTC")

    start = ref_ts - pd.Timedelta(days=lookback_days)

    carbon = fetch_carbon_intensity(start.to_pydatetime(), ref_ts.to_pydatetime())
    fuelinst = fetch_fuelinst_context(start.to_pydatetime(), ref_ts.to_pydatetime())

    frames: list[pd.DataFrame] = []
    if not carbon.empty:
        frames.append(carbon.rename("carbon_intensity").to_frame())
    if not fuelinst.empty:
        frames.append(fuelinst)

    if not frames:
        return pd.DataFrame(
            columns=[
                "carbon_intensity",
                "gas_mw",
                "wind_mw",
                "nuclear_mw",
                "pumped_storage_mw",
                "interconnector_net_mw",
            ]
        )

    merged = pd.concat(frames, axis=1).sort_index().ffill().bfill()
    for col in [
        "carbon_intensity",
        "gas_mw",
        "wind_mw",
        "nuclear_mw",
        "pumped_storage_mw",
        "interconnector_net_mw",
    ]:
        if col not in merged.columns:
            merged[col] = 0.0

    return merged[
        [
            "carbon_intensity",
            "gas_mw",
            "wind_mw",
            "nuclear_mw",
            "pumped_storage_mw",
            "interconnector_net_mw",
        ]
    ]
