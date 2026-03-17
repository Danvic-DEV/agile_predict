from datetime import datetime

from pydantic import BaseModel


class ForecastSummary(BaseModel):
    id: int
    name: str
    created_at: datetime


class AgilePricePoint(BaseModel):
    date_time: datetime
    agile_pred: float
    agile_low: float | None = None
    agile_high: float | None = None
    region: str | None = None


class ForecastWithPrices(BaseModel):
    id: int
    name: str
    created_at: datetime
    prices: list[AgilePricePoint]


class ForecastDataPoint(BaseModel):
    date_time: datetime
    day_ahead: float | None
    bm_wind: float
    solar: float
    emb_wind: float
    temp_2m: float
    wind_10m: float
    rad: float
    demand: float
