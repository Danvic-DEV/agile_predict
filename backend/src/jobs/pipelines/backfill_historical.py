"""
Historical data backfill pipeline to improve ML training quality.

Fetches historical weather data (wind, solar, demand) from NESO APIs
and pairs it with actual Agile prices to create rich training dataset.

This solves the overfitting problem caused by training on only 6 days
of Nordpool data when we have 5+ weeks of actual Agile price history.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import pandas as pd
from sqlalchemy import select

from src.ml.ingest.grid_weather import (
    _fetch_neso_bm_wind,
    _fetch_neso_demand,
    _fetch_neso_solar_wind,
    _fetch_open_meteo,
)
from src.repositories.sql_models import AgileActualORM, ForecastORM, ForecastDataORM
from src.repositories.unit_of_work import UnitOfWork
from src.schemas.forecast import ForecastDataCreate, ForecastCreate

log = logging.getLogger(__name__)


def fetch_historical_weather(start_date: str, end_date: str | None = None) -> pd.DataFrame:
    """
    Fetch historical weather features from NESO and Open-Meteo APIs.
    
    Returns DataFrame with columns: bm_wind, solar, emb_wind, demand, temp_2m, wind_10m, rad
    Indexed by UTC datetime with 30-minute frequency.
    """
    log.info(f"Fetching historical weather data from {start_date} to {end_date or 'now'}")
    
    # Fetch historical grid data from NESO
    bm_wind = _fetch_neso_bm_wind(start_date)
    solar_wind_df = _fetch_neso_solar_wind(start_date)
    demand = _fetch_neso_demand(start_date)
    
    # Combine grid data
    df = pd.DataFrame(index=pd.DatetimeIndex([], tz='UTC'))
    if not bm_wind.empty:
        df['bm_wind'] = bm_wind
    if not solar_wind_df.empty:
        df['solar'] = solar_wind_df['solar']
        df['emb_wind'] = solar_wind_df['total_wind'] - bm_wind if not bm_wind.empty else solar_wind_df['total_wind']
    if not demand.empty:
        df['demand'] = demand
    
    # Fetch historical weather from Open-Meteo
    try:
        weather_df = _fetch_open_meteo(start_date, end_date or datetime.now(timezone.utc).strftime("%Y-%m-%d"))
        if not weather_df.empty:
            for col in ['temp_2m', 'wind_10m', 'rad']:
                if col in weather_df.columns:
                    df[col] = weather_df[col]
    except Exception as exc:
        log.warning(f"Open-Meteo historical fetch failed: {exc}")
    
    # Fill forward gaps (historical data may have some missing periods)
    df = df.sort_index().ffill().bfill()
    
    log.info(f"Fetched {len(df)} historical weather rows from {df.index.min()} to {df.index.max()}")
    return df


def create_backfill_forecasts(
    uow: UnitOfWork,
    start_date: datetime,
    end_date: datetime,
    region: str = "B",
) -> tuple[int, int]:
    """
    Create historical backfill forecasts by pairing historical weather with actual Agile prices.
    
    Returns: (num_forecasts_created, num_data_rows_created)
    """
    log.info(f"Starting historical backfill for region {region} from {start_date} to {end_date}")
    
    # Check if we have actual Agile prices for this period
    agile_prices = uow.session.execute(
        select(AgileActualORM)
        .where(
            AgileActualORM.region == region.upper(),
            AgileActualORM.date_time >= start_date,
            AgileActualORM.date_time <= end_date,
        )
        .order_by(AgileActualORM.date_time)
    ).scalars().all()
    
    if not agile_prices:
        raise ValueError(f"No actual Agile prices found for region {region} in the specified period")
    
    log.info(f"Found {len(agile_prices)} actual Agile price rows for region {region}")
    
    # Fetch historical weather data
    start_str = start_date.strftime("%Y-%m-%d")
    end_str = end_date.strftime("%Y-%m-%d")
    weather_df = fetch_historical_weather(start_str, end_str)
    
    if weather_df.empty:
        raise ValueError("Failed to fetch historical weather data")
    
    # Group by date and create one backfill forecast per day
    # This mimics the structure of regular forecasts but represents historical data
    dates = pd.date_range(start_date.date(), end_date.date(), freq='D')
    
    forecasts_created = 0
    data_rows_created = 0
    
    for date in dates:
        forecast_name = f"backfill::region-{region}::{date.strftime('%Y-%m-%d')}"
        
        # Check if this backfill forecast already exists
        existing = uow.session.execute(
            select(ForecastORM).where(ForecastORM.name == forecast_name)
        ).scalar_one_or_none()
        
        if existing:
            log.debug(f"Backfill forecast {forecast_name} already exists, skipping")
            continue
        
        # Create forecast representing this historical day
        forecast = ForecastCreate(
            name=forecast_name,
            region=region.upper(),
            created_at=datetime.combine(date.date(), datetime.min.time(), tzinfo=timezone.utc),
        )
        
        forecast_orm = ForecastORM(**forecast.model_dump())
        uow.session.add(forecast_orm)
        uow.session.flush()  # Get the forecast ID
        
        # Get weather data for the forecast horizon (next 13 days from this historical point)
        forecast_start = pd.Timestamp(date, tz='UTC') + pd.Timedelta(hours=22)
        forecast_end = forecast_start + pd.Timedelta(days=13)
        
        weather_slice = weather_df[
            (weather_df.index >= forecast_start) & 
            (weather_df.index < forecast_end)
        ].copy()
        
        if weather_slice.empty:
            log.warning(f"No weather data for forecast {forecast_name}, skipping")
            continue
        
        # Create ForecastDataORM rows
        for idx, row in weather_slice.iterrows():
            data_row = ForecastDataORM(
                forecast_id=forecast_orm.id,
                date_time=idx.to_pydatetime(),
                bm_wind=float(row.get('bm_wind', 0.0)),
                solar=float(row.get('solar', 0.0)),
                emb_wind=float(row.get('emb_wind', 0.0)),
                demand=float(row.get('demand', 0.0)),
                temp_2m=float(row.get('temp_2m', 0.0)),
                wind_10m=float(row.get('wind_10m', 0.0)),
                rad=float(row.get('rad', 0.0)),
            )
            uow.session.add(data_row)
            data_rows_created += 1
        
        forecasts_created += 1
        log.info(f"Created backfill forecast {forecast_name} with {len(weather_slice)} data rows")
    
    uow.session.commit()
    log.info(f"Backfill complete: {forecasts_created} forecasts, {data_rows_created} data rows")
    
    return forecasts_created, data_rows_created


def run_backfill_job(uow: UnitOfWork, region: str = "B") -> dict:
    """
    Main entry point for historical backfill job.
    
    Determines appropriate date range based on available Agile actuals
    and creates backfill forecasts.
    """
    # Find the earliest and latest actual Agile price dates
    earliest = uow.session.execute(
        select(AgileActualORM.date_time)
        .where(AgileActualORM.region == region.upper())
        .order_by(AgileActualORM.date_time.asc())
        .limit(1)
    ).scalar_one_or_none()
    
    latest = uow.session.execute(
        select(AgileActualORM.date_time)
        .where(AgileActualORM.region == region.upper())
        .order_by(AgileActualORM.date_time.desc())
        .limit(1)
    ).scalar_one_or_none()
    
    if not earliest or not latest:
        raise ValueError(f"No actual Agile prices found for region {region}")
    
    log.info(f"Agile actuals available from {earliest} to {latest}")
    
    # Create backfill forecasts for the entire available period
    forecasts_created, data_rows_created = create_backfill_forecasts(
        uow=uow,
        start_date=earliest,
        end_date=latest,
        region=region,
    )
    
    return {
        "status": "success",
        "region": region,
        "period_start": earliest.isoformat(),
        "period_end": latest.isoformat(),
        "forecasts_created": forecasts_created,
        "data_rows_created": data_rows_created,
    }
