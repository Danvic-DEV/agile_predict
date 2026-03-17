from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class AgileDataWrite:
    forecast_id: int
    region: str
    agile_pred: float
    agile_low: float
    agile_high: float
    date_time: datetime


@dataclass(frozen=True)
class ForecastDataWrite:
    forecast_id: int
    date_time: datetime
    day_ahead: float | None
    bm_wind: float
    solar: float
    emb_wind: float
    temp_2m: float
    wind_10m: float
    rad: float
    demand: float
