from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from src.core.regions import REGION_FACTORS, normalize_region
from src.repositories.types import AgileDataWrite, ForecastDataWrite

_HISTORY_NAME_PREFIX = "bundle::history-"


class _ForecastRow(Protocol):
    id: int
    name: str
    created_at: datetime
    mean: float | None
    stdev: float | None


class _ForecastWrites(Protocol):
    def get_by_name(self, name: str) -> _ForecastRow | None:
        ...

    def create_forecast(
        self,
        name: str,
        created_at: datetime,
        mean: float | None = None,
        stdev: float | None = None,
    ) -> _ForecastRow:
        ...

    def list_older_than(self, cutoff: datetime) -> list[_ForecastRow]:
        ...

    def delete_by_ids(self, ids: list[int]) -> int:
        ...


class _ForecastDataWrites(Protocol):
    def delete_for_forecast(self, forecast_id: int) -> int:
        ...

    def delete_for_forecasts(self, forecast_ids: list[int]) -> int:
        ...

    def bulk_insert(self, rows: list[ForecastDataWrite]) -> int:
        ...


class _AgileDataWrites(Protocol):
    def delete_for_forecast(self, forecast_id: int) -> int:
        ...

    def delete_for_forecasts(self, forecast_ids: list[int]) -> int:
        ...

    def bulk_insert(self, rows: list[AgileDataWrite]) -> int:
        ...


class BootstrapBundleUoW(Protocol):
    forecast_writes: _ForecastWrites
    forecast_data_writes: _ForecastDataWrites
    agile_data_writes: _AgileDataWrites
    session: Any  # SQLAlchemy session for direct queries


@dataclass(frozen=True)
class BootstrapBundleConfig:
    points: int = 48
    idempotency_key: str | None = None
    forecast_name: str | None = None
    replace_existing: bool = True
    regions: tuple[str, ...] = ("X", "G")

    day_ahead_base: float = 80.0
    day_ahead_step: float = 0.35
    day_ahead_values: tuple[float, ...] | None = None
    day_ahead_low_values: tuple[float, ...] | None = None
    day_ahead_high_values: tuple[float, ...] | None = None
    feature_rows: tuple["HistoryForecastFeatureRow", ...] | None = None
    forecast_mean: float | None = None
    forecast_stdev: float | None = None

    bm_wind_base: float = 5000.0
    solar_base: float = 1500.0
    emb_wind_base: float = 1200.0
    temp_2m_base: float = 10.0
    wind_10m_base: float = 7.5
    rad_base: float = 120.0
    demand_base: float = 30000.0

    write_agile_data: bool = True
    agile_spread: float = 1.5


@dataclass(frozen=True)
class BootstrapBundleResult:
    forecast_name: str
    forecast_id: int
    forecast_data_points_written: int
    agile_data_points_written: int
    regions: tuple[str, ...]
    created_at: datetime
    idempotent_hit: bool


@dataclass(frozen=True)
class HistoryForecastFeatureRow:
    """Real feature values for a single 30-minute slot."""

    date_time: datetime
    bm_wind: float
    solar: float
    emb_wind: float
    temp_2m: float
    wind_10m: float
    rad: float
    demand: float
    day_ahead: float | None = None


def _day_ahead_to_agile(value: float, dt: datetime, region: str) -> float:
    mult, peak_offset = REGION_FACTORS[region]
    agile = value * mult
    gb_hour = dt.astimezone(ZoneInfo("Europe/London")).hour
    if 16 <= gb_hour < 19:
        agile += peak_offset
    return agile


def _align_to_half_hour(dt: datetime) -> datetime:
    aligned = dt.astimezone(timezone.utc).replace(second=0, microsecond=0)
    minute = aligned.minute
    if minute < 30:
        return aligned.replace(minute=0)
    return aligned.replace(minute=30)


def write_bootstrap_bundle(uow: BootstrapBundleUoW, config: BootstrapBundleConfig) -> BootstrapBundleResult:
    now = datetime.now(timezone.utc)
    forecast_name = now.strftime("%Y-%m-%d %H:%M:%S.%f")
    if config.forecast_name:
        forecast_name = config.forecast_name
    elif config.idempotency_key:
        forecast_name = f"bundle::{config.idempotency_key}"

    regions = tuple(normalize_region(r) for r in dict.fromkeys(config.regions))
    idempotent_hit = False

    forecast = uow.forecast_writes.get_by_name(forecast_name)
    if forecast is None:
        forecast = uow.forecast_writes.create_forecast(
            name=forecast_name,
            created_at=now,
            mean=config.forecast_mean,
            stdev=config.forecast_stdev,
        )
    else:
        idempotent_hit = True
        if config.forecast_mean is not None:
            forecast.mean = config.forecast_mean
        if config.forecast_stdev is not None:
            forecast.stdev = config.forecast_stdev
        if config.replace_existing:
            uow.forecast_data_writes.delete_for_forecast(forecast.id)
            uow.agile_data_writes.delete_for_forecast(forecast.id)

    anchor_time = _align_to_half_hour(forecast.created_at if config.idempotency_key else now)

    day_ahead_values = tuple(float(v) for v in config.day_ahead_values) if config.day_ahead_values else None
    day_ahead_low_values = tuple(float(v) for v in config.day_ahead_low_values) if config.day_ahead_low_values else None
    day_ahead_high_values = tuple(float(v) for v in config.day_ahead_high_values) if config.day_ahead_high_values else None
    feature_rows = tuple(config.feature_rows) if config.feature_rows else None
    points = len(day_ahead_values) if day_ahead_values is not None else config.points
    if feature_rows is not None and len(feature_rows) < points:
        raise ValueError(f"insufficient feature rows for forecast points: have={len(feature_rows)} need={points}")

    forecast_data_rows: list[ForecastDataWrite] = []
    agile_rows: list[AgileDataWrite] = []
    for i in range(points):
        feature_row = feature_rows[i] if feature_rows is not None else None
        dt = feature_row.date_time if feature_row is not None else anchor_time + timedelta(minutes=30 * i)
        day_ahead = day_ahead_values[i] if day_ahead_values is not None else config.day_ahead_base + config.day_ahead_step * (i % 16)
        day_ahead_low = day_ahead_low_values[i] if day_ahead_low_values is not None else None
        day_ahead_high = day_ahead_high_values[i] if day_ahead_high_values is not None else None

        forecast_data_rows.append(
            ForecastDataWrite(
                forecast_id=forecast.id,
                date_time=dt,
                day_ahead=round(day_ahead, 4),
                bm_wind=(feature_row.bm_wind if feature_row is not None else config.bm_wind_base + (i % 10) * 40),
                solar=(feature_row.solar if feature_row is not None else config.solar_base + (i % 8) * 25),
                emb_wind=(feature_row.emb_wind if feature_row is not None else config.emb_wind_base + (i % 6) * 18),
                temp_2m=(feature_row.temp_2m if feature_row is not None else config.temp_2m_base + (i % 12) * 0.2),
                wind_10m=(feature_row.wind_10m if feature_row is not None else config.wind_10m_base + (i % 9) * 0.15),
                rad=(feature_row.rad if feature_row is not None else config.rad_base + (i % 10) * 3),
                demand=(feature_row.demand if feature_row is not None else config.demand_base + (i % 12) * 55),
            )
        )

        if config.write_agile_data:
            for region in regions:
                pred = _day_ahead_to_agile(day_ahead, dt, region)
                if (day_ahead_low is not None) and (day_ahead_high is not None):
                    low = _day_ahead_to_agile(day_ahead_low, dt, region)
                    high = _day_ahead_to_agile(day_ahead_high, dt, region)
                    agile_low = min(low, pred, high)
                    agile_high = max(low, pred, high)
                else:
                    agile_low = pred - config.agile_spread
                    agile_high = pred + config.agile_spread

                agile_rows.append(
                    AgileDataWrite(
                        forecast_id=forecast.id,
                        region=region,
                        agile_pred=round(pred, 4),
                        agile_low=round(agile_low, 4),
                        agile_high=round(agile_high, 4),
                        date_time=dt,
                    )
                )

    forecast_data_written = uow.forecast_data_writes.bulk_insert(forecast_data_rows)
    agile_written = uow.agile_data_writes.bulk_insert(agile_rows)

    return BootstrapBundleResult(
        forecast_name=forecast.name,
        forecast_id=forecast.id,
        forecast_data_points_written=forecast_data_written,
        agile_data_points_written=agile_written,
        regions=regions,
        created_at=forecast.created_at,
        idempotent_hit=idempotent_hit,
    )


def prune_old_forecasts(uow: BootstrapBundleUoW, max_age_days: int = 730) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    old_forecasts = uow.forecast_writes.list_older_than(cutoff)
    old_history = [f for f in old_forecasts if f.name.startswith(_HISTORY_NAME_PREFIX)]
    if not old_history:
        return 0

    ids = [f.id for f in old_history]
    uow.forecast_data_writes.delete_for_forecasts(ids)
    uow.agile_data_writes.delete_for_forecasts(ids)
    uow.forecast_writes.delete_by_ids(ids)

def prune_update_job_forecasts(uow: BootstrapBundleUoW, keep_count: int = 3) -> int:
    """Keep only the most recent N update-job forecasts, delete older ones."""
    from sqlalchemy import select
    from src.repositories.sql_models import ForecastORM
    
    # Get all update-job forecasts ordered by created_at descending
    stmt = (
        select(ForecastORM)
        .where(ForecastORM.name.like("bundle::update-job-%"))
        .order_by(ForecastORM.created_at.desc())
    )
    forecasts = uow.session.execute(stmt).scalars().all()
    
    if len(forecasts) <= keep_count:
        return 0
    
    # Delete all forecasts beyond the keep_count
    to_delete = forecasts[keep_count:]
    ids = [f.id for f in to_delete]
    
    uow.forecast_data_writes.delete_for_forecasts(ids)
    uow.agile_data_writes.delete_for_forecasts(ids)
    return uow.forecast_writes.delete_by_ids(ids)    return len(ids)


def write_history_forecast(
    uow: BootstrapBundleUoW,
    feature_rows: list[HistoryForecastFeatureRow],
    now: datetime | None = None,
    regions: tuple[str, ...] = ("X", "G"),
    forecast_mean: float | None = None,
    forecast_stdev: float | None = None,
) -> BootstrapBundleResult:
    ref = now or datetime.now(timezone.utc)
    key = ref.strftime("%Y-%m-%dT%H:%M")
    forecast_name = f"{_HISTORY_NAME_PREFIX}{key}"
    norm_regions = tuple(normalize_region(r) for r in dict.fromkeys(regions))

    forecast = uow.forecast_writes.get_by_name(forecast_name)
    if forecast is None:
        forecast = uow.forecast_writes.create_forecast(
            name=forecast_name,
            created_at=ref,
            mean=forecast_mean,
            stdev=forecast_stdev,
        )
        idempotent_hit = False
    else:
        idempotent_hit = True
        uow.forecast_data_writes.delete_for_forecast(forecast.id)
        uow.agile_data_writes.delete_for_forecast(forecast.id)

    fd_rows: list[ForecastDataWrite] = []
    agile_rows: list[AgileDataWrite] = []
    for row in feature_rows:
        fd_rows.append(
            ForecastDataWrite(
                forecast_id=forecast.id,
                date_time=row.date_time,
                day_ahead=row.day_ahead,
                bm_wind=row.bm_wind,
                solar=row.solar,
                emb_wind=row.emb_wind,
                temp_2m=row.temp_2m,
                wind_10m=row.wind_10m,
                rad=row.rad,
                demand=row.demand,
            )
        )
        for region in norm_regions:
            pred = _day_ahead_to_agile(row.day_ahead or 0.0, row.date_time, region)
            agile_rows.append(
                AgileDataWrite(
                    forecast_id=forecast.id,
                    region=region,
                    agile_pred=round(pred, 4),
                    agile_low=round(pred - 1.5, 4),
                    agile_high=round(pred + 1.5, 4),
                    date_time=row.date_time,
                )
            )

    fd_written = uow.forecast_data_writes.bulk_insert(fd_rows)
    agile_written = uow.agile_data_writes.bulk_insert(agile_rows)

    return BootstrapBundleResult(
        forecast_name=forecast.name,
        forecast_id=forecast.id,
        forecast_data_points_written=fd_written,
        agile_data_points_written=agile_written,
        regions=norm_regions,
        created_at=forecast.created_at,
        idempotent_hit=idempotent_hit,
    )
