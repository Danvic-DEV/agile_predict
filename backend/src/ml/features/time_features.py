from __future__ import annotations

import pandas as pd


def add_time_features(df: pd.DataFrame, timestamp_column: str | None = None) -> pd.DataFrame:
    out = df.copy()

    if timestamp_column:
        ts = pd.to_datetime(out[timestamp_column])
    else:
        # Normalize DatetimeIndex inputs to a Series so `.dt` access is always valid.
        ts = pd.Series(pd.to_datetime(out.index), index=out.index)

    if ts.dt.tz is None:
        ts = ts.dt.tz_localize("UTC")

    gb = ts.dt.tz_convert("GB")
    out["time"] = gb.dt.hour + gb.dt.minute / 60
    out["day_of_week"] = gb.dt.day_of_week.astype(int)
    out["weekend"] = (out["day_of_week"] >= 5).astype(int)
    out["peak"] = ((gb.dt.hour >= 16) & (gb.dt.hour < 19)).astype(int)
    return out
