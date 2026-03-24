from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException

from src.api.v1.deps import UnitOfWorkDep
from src.api.errors import http_error
from src.core.discord_notifications import send_update_failure_notification, send_update_started_notification
from src.core.regions import normalize_region
from src.domain.bootstrap_bundle import BootstrapBundleConfig, write_bootstrap_bundle
from src.jobs.pipelines.backfill_historical import run_backfill_job
from src.jobs.pipelines.update_forecast import run_update_forecast_job
from src.core.feed_health import FEED_SOURCES
from src.ml.ingest import fetch_day_ahead_prices
from src.ml.ingest.grid_weather import (
    _fetch_elexon_ndf_forecast,
    _fetch_neso_bm_wind,
    _fetch_neso_demand,
    _fetch_neso_embedded,
    _fetch_neso_solar_wind,
    _fetch_open_meteo,
)
from src.ml.ingest.octopus_agile import _resolve_agile_product_id, fetch_agile_prices_for_region, fetch_agile_prices_all_regions
from src.ml.ingest.system_context import fetch_fuelinst_context
from src.repositories.types import AgileDataWrite
from src.schemas.admin_jobs import (
    BackfillAgilePricesResponse,
    BootstrapForecastBundleRequest,
    BootstrapForecastBundleResponse,
    BootstrapForecastRequest,
    BootstrapForecastResponse,
    RefreshFeedResponse,
    RunBackfillResponse,
    RunUpdateJobResponse,
)

router = APIRouter()


def _refresh_feed_source(source_id: str) -> int:
    if source_id not in FEED_SOURCES:
        raise ValueError(f"Unsupported feed source: {source_id}")

    now_utc = datetime.now(timezone.utc)
    start_date = (now_utc - timedelta(days=3)).strftime("%Y-%m-%d")

    if source_id.startswith("agile_octopus_"):
        region = source_id.removeprefix("agile_octopus_").upper()
        product_id = _resolve_agile_product_id()
        prices = fetch_agile_prices_for_region(
            region=region,
            product_id=product_id,
            from_date=now_utc - timedelta(days=3),
            to_date=now_utc + timedelta(days=1),
        )
        return len(prices)

    if source_id == "nordpool_da":
        prices = fetch_day_ahead_prices(now=now_utc)
        return len(prices)

    if source_id == "weather_open_meteo":
        weather = _fetch_open_meteo(
            start_date=start_date,
            end_date=(now_utc + timedelta(days=3)).strftime("%Y-%m-%d"),
        )
        return len(weather)

    if source_id == "neso_demand":
        return len(_fetch_neso_demand(start_date))

    if source_id == "neso_bm_wind":
        return len(_fetch_neso_bm_wind(start_date))

    if source_id == "neso_solar_wind":
        return len(_fetch_neso_solar_wind(start_date))

    if source_id == "neso_embedded_solar_wind":
        return len(_fetch_neso_embedded(start_date))

    if source_id == "elexon_indo":
        return len(_fetch_neso_demand(start_date))

    if source_id == "elexon_ndf":
        return len(_fetch_elexon_ndf_forecast())

    if source_id == "elexon_fuelinst":
        context = fetch_fuelinst_context(now_utc - timedelta(days=3), now_utc)
        return len(context)

    raise ValueError(f"Unsupported feed source: {source_id}")


@router.post("/bootstrap-forecast", response_model=BootstrapForecastResponse)
def bootstrap_forecast(
    payload: BootstrapForecastRequest,
    uow: UnitOfWorkDep,
) -> BootstrapForecastResponse:
    now = datetime.now(timezone.utc)
    forecast_name = now.strftime("%Y-%m-%d %H:%M:%S.%f")
    if payload.idempotency_key:
        forecast_name = f"bootstrap::{payload.idempotency_key}"

    try:
        regions = [normalize_region(r) for r in dict.fromkeys(payload.regions)]
        idempotent_hit = False

        forecast = uow.forecast_writes.get_by_name(forecast_name)
        if forecast is None:
            forecast = uow.forecast_writes.create_forecast(
                name=forecast_name,
                created_at=now,
                mean=None,
                stdev=None,
            )
        else:
            idempotent_hit = True
            if payload.replace_existing:
                uow.agile_data_writes.delete_for_forecast(forecast.id)

        anchor_time = forecast.created_at if payload.idempotency_key else now

        rows: list[AgileDataWrite] = []
        for region in regions:
            for i in range(payload.points):
                dt = anchor_time + timedelta(minutes=30 * i)
                # Deterministic seed data for API and parity testing infrastructure.
                pred = payload.base_price + (i % 8) * 0.25
                rows.append(
                    AgileDataWrite(
                        forecast_id=forecast.id,
                        region=region,
                        agile_pred=round(pred, 4),
                        agile_low=round(pred - payload.spread, 4),
                        agile_high=round(pred + payload.spread, 4),
                        date_time=dt,
                    )
                )

        written = uow.agile_data_writes.bulk_insert(rows)
        uow.commit()

    except Exception as exc:
        uow.rollback()
        raise http_error(400, "bootstrap_forecast_failed", "Bootstrap forecast failed.", exc) from exc

    return BootstrapForecastResponse(
        forecast_name=forecast.name,
        forecast_id=forecast.id,
        points_written=written,
        regions=regions,
        created_at=forecast.created_at,
        idempotent_hit=idempotent_hit,
    )


@router.post("/bootstrap-forecast-bundle", response_model=BootstrapForecastBundleResponse)
def bootstrap_forecast_bundle(
    payload: BootstrapForecastBundleRequest,
    uow: UnitOfWorkDep,
) -> BootstrapForecastBundleResponse:
    try:
        result = write_bootstrap_bundle(
            uow=uow,
            config=BootstrapBundleConfig(
                points=payload.points,
                idempotency_key=payload.idempotency_key,
                replace_existing=payload.replace_existing,
                regions=tuple(payload.regions),
                day_ahead_base=payload.day_ahead_base,
                day_ahead_step=payload.day_ahead_step,
                bm_wind_base=payload.bm_wind_base,
                solar_base=payload.solar_base,
                emb_wind_base=payload.emb_wind_base,
                temp_2m_base=payload.temp_2m_base,
                wind_10m_base=payload.wind_10m_base,
                rad_base=payload.rad_base,
                demand_base=payload.demand_base,
                write_agile_data=payload.write_agile_data,
                agile_spread=payload.agile_spread,
            ),
        )
        uow.commit()

    except Exception as exc:
        uow.rollback()
        raise http_error(400, "bootstrap_forecast_bundle_failed", "Bootstrap forecast bundle failed.", exc) from exc

    return BootstrapForecastBundleResponse(
        forecast_name=result.forecast_name,
        forecast_id=result.forecast_id,
        forecast_data_points_written=result.forecast_data_points_written,
        agile_data_points_written=result.agile_data_points_written,
        regions=list(result.regions),
        created_at=result.created_at,
        idempotent_hit=result.idempotent_hit,
    )


@router.post("/run-update-forecast-job", response_model=RunUpdateJobResponse)
def run_update_job(uow: UnitOfWorkDep) -> RunUpdateJobResponse:
    try:
        send_update_started_notification(trigger="manual")
        result = run_update_forecast_job(uow=uow)
        uow.commit()
    except Exception as exc:
        uow.rollback()
        send_update_failure_notification(detail=str(exc), trigger="manual")
        raise http_error(400, "update_forecast_job_failed", "Update forecast job failed.", exc) from exc

    return RunUpdateJobResponse(
        forecast_name=result.forecast_name,
        records_written=result.records_written,
        source=result.source,
        day_ahead_points=result.day_ahead_points,
    )


@router.post("/refresh-feed/{source_id}", response_model=RefreshFeedResponse)
def refresh_feed(source_id: str) -> RefreshFeedResponse:
    try:
        records_received = _refresh_feed_source(source_id)
    except ValueError as exc:
        raise http_error(400, "invalid_feed_source", "Feed refresh failed.", exc) from exc
    except Exception as exc:
        raise http_error(400, "feed_refresh_failed", "Feed refresh failed.", exc) from exc

    refreshed_at = datetime.now(timezone.utc)
    return RefreshFeedResponse(
        source_id=source_id,
        records_received=records_received,
        refreshed_at=refreshed_at,
        detail=f"Feed refresh completed for {source_id}.",
    )


@router.post("/run-backfill-historical/{region}", response_model=RunBackfillResponse)
def run_backfill_historical(region: str, uow: UnitOfWorkDep) -> RunBackfillResponse:
    """
    Backfill historical weather data paired with actual Agile prices.
    
    Creates forecast records representing historical weather snapshots,
    dramatically expanding ML training dataset from ~144 to ~22,000+ price points.
    """
    try:
        normalized_region = normalize_region(region)
        result = run_backfill_job(uow=uow, region=normalized_region)
        
        return RunBackfillResponse(
            status=result["status"],
            region=result["region"],
            period_start=result["period_start"],
            period_end=result["period_end"],
            forecasts_created=result["forecasts_created"],
            data_rows_created=result["data_rows_created"],
            detail=f"Backfill completed: {result['forecasts_created']} forecasts with {result['data_rows_created']} weather data rows",
        )
    except ValueError as exc:
        raise http_error(400, "backfill_validation_failed", "Historical backfill failed.", exc) from exc
    except Exception as exc:
        raise http_error(500, "backfill_execution_failed", "Historical backfill failed.", exc) from exc


@router.post("/backfill-agile-prices", response_model=BackfillAgilePricesResponse)
def backfill_agile_prices(
    uow: UnitOfWorkDep,
    days: int = 730,  # Default 2 years
    regions: list[str] | None = None,
) -> BackfillAgilePricesResponse:
    """
    Deep backfill of historical Agile prices from Octopus API.
    
    Fetches ALL available historical Agile actual prices for specified regions
    (or all 15 regions). This dramatically expands the training dataset by going
    back as far as the Octopus API allows (potentially years of data).
    
    Args:
        days: Number of days to backfill (default 730 = 2 years)
        regions: List of regions to backfill (default: all 15 UK regions)
    
    Returns:
        Summary of prices fetched and upserted
    """
    try:
        now_utc = datetime.now(timezone.utc)
        from_date = now_utc - timedelta(days=days)
        to_date = now_utc + timedelta(days=2)  # Include forward-released prices
        
        # Default to all regions if not specified - let Octopus API discover them
        # rather than hardcoding a list that may be wrong
        if not regions:
            regions = None  # fetch_agile_prices_all_regions auto-discovers
            normalized_regions = []
        else:
            normalized_regions = [normalize_region(r) for r in regions]
        
        # Fetch prices for all regions
        agile_prices_by_region = fetch_agile_prices_all_regions(
            from_date=from_date,
            to_date=to_date,
            timeout=30,
            regions=normalized_regions if normalized_regions else None,
        )
        
        # Flatten to upsert rows
        agile_rows = []
        for region, prices_dict in agile_prices_by_region.items():
            for dt, price in prices_dict.items():
                agile_rows.append({
                    "date_time": dt,
                    "region": region,
                    "agile_actual": float(price),
                })
        
        # Use the actual regions returned by the API for accurate reporting
        fetched_regions = sorted(agile_prices_by_region.keys())
        
        if agile_rows:
            upsert_count = uow.agile_actual_writes.upsert_many(agile_rows)
            uow.commit()
            
            # Find actual date range
            all_dates = [row["date_time"] for row in agile_rows]
            min_date = min(all_dates)
            max_date = max(all_dates)
            
            return BackfillAgilePricesResponse(
                regions_processed=fetched_regions,
                total_prices_upserted=upsert_count,
                period_start=min_date.isoformat(),
                period_end=max_date.isoformat(),
                detail=f"Successfully backfilled {upsert_count} Agile prices across {len(fetched_regions)} regions from {min_date.date()} to {max_date.date()}",
            )
        else:
            return BackfillAgilePricesResponse(
                regions_processed=fetched_regions,
                total_prices_upserted=0,
                period_start="",
                period_end="",
                detail="No Agile prices found for specified date range and regions",
            )
    except ValueError as exc:
        raise http_error(400, "agile_backfill_validation_failed", "Agile backfill failed.", exc) from exc
    except Exception as exc:
        raise http_error(500, "agile_backfill_execution_failed", "Agile backfill failed.", exc) from exc
