from datetime import datetime

from pydantic import BaseModel, Field


class BootstrapForecastRequest(BaseModel):
    points: int = Field(default=48, ge=1, le=672)
    regions: list[str] = Field(default_factory=lambda: ["X", "G"])
    base_price: float = 22.0
    spread: float = 1.5
    idempotency_key: str | None = Field(default=None, min_length=3, max_length=64)
    replace_existing: bool = True


class BootstrapForecastResponse(BaseModel):
    forecast_name: str
    forecast_id: int
    points_written: int
    regions: list[str]
    created_at: datetime
    idempotent_hit: bool


class BootstrapForecastBundleRequest(BaseModel):
    points: int = Field(default=48, ge=1, le=672)
    idempotency_key: str | None = Field(default=None, min_length=3, max_length=64)
    replace_existing: bool = True
    regions: list[str] = Field(default_factory=lambda: ["X", "G"])

    day_ahead_base: float = 80.0
    day_ahead_step: float = 0.35

    bm_wind_base: float = 5000.0
    solar_base: float = 1500.0
    emb_wind_base: float = 1200.0
    temp_2m_base: float = 10.0
    wind_10m_base: float = 7.5
    rad_base: float = 120.0
    demand_base: float = 30000.0

    write_agile_data: bool = True
    agile_spread: float = 1.5


class BootstrapForecastBundleResponse(BaseModel):
    forecast_name: str
    forecast_id: int
    forecast_data_points_written: int
    agile_data_points_written: int
    regions: list[str]
    created_at: datetime
    idempotent_hit: bool


class RunUpdateJobResponse(BaseModel):
    forecast_name: str
    records_written: int
    source: str | None = None
    day_ahead_points: int | None = None


class RefreshFeedResponse(BaseModel):
    source_id: str
    records_received: int
    refreshed_at: datetime
    detail: str


class RunBackfillResponse(BaseModel):
    status: str
    region: str
    period_start: str
    period_end: str
    forecasts_created: int
    data_rows_created: int
    detail: str


class BackfillAgilePricesResponse(BaseModel):
    regions_processed: list[str]
    total_prices_upserted: int
    period_start: str
    period_end: str
    detail: str
