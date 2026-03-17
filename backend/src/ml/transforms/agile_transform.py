from __future__ import annotations

import pandas as pd

from src.core.regions import REGION_FACTORS, normalize_region


def day_ahead_to_agile(series: pd.Series, region: str) -> pd.Series:
    region_key = normalize_region(region)
    mult, peak_offset = REGION_FACTORS[region_key]

    result = series.copy().astype(float)
    index = pd.to_datetime(result.index)
    if getattr(index, "tz", None) is None:
        index = index.tz_localize("UTC")
    index = index.tz_convert("GB")

    peak = (index.hour >= 16) & (index.hour < 19)
    result = result * mult
    result.loc[peak] = result.loc[peak] + peak_offset
    result.name = "agile"
    return result


def agile_to_day_ahead(series: pd.Series, region: str) -> pd.Series:
    region_key = normalize_region(region)
    mult, peak_offset = REGION_FACTORS[region_key]

    result = series.copy().astype(float)
    index = pd.to_datetime(result.index)
    if getattr(index, "tz", None) is None:
        index = index.tz_localize("UTC")
    index = index.tz_convert("GB")

    peak = (index.hour >= 16) & (index.hour < 19)
    result.loc[peak] = result.loc[peak] - peak_offset
    result = result / mult
    result.name = "day_ahead"
    return result
