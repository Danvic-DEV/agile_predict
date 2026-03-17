from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException

from src.api.v1.deps import UnitOfWorkDep
from src.core.regions import normalize_region
from src.domain.bootstrap_bundle import BootstrapBundleConfig, write_bootstrap_bundle
from src.jobs.pipelines.update_forecast import run_update_forecast_job
from src.schemas.admin_jobs import (
    BootstrapForecastBundleRequest,
    BootstrapForecastBundleResponse,
    BootstrapForecastRequest,
    BootstrapForecastResponse,
    RunUpdateJobResponse,
)

router = APIRouter()


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
        raise HTTPException(status_code=400, detail=f"bootstrap forecast failed: {exc}") from exc

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
        raise HTTPException(status_code=400, detail=f"bootstrap forecast bundle failed: {exc}") from exc

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
        result = run_update_forecast_job(uow=uow)
        uow.commit()
    except Exception as exc:
        uow.rollback()
        raise HTTPException(status_code=400, detail=f"update forecast job failed: {exc}") from exc

    return RunUpdateJobResponse(
        forecast_name=result.forecast_name,
        records_written=result.records_written,
        source=result.source,
        day_ahead_points=result.day_ahead_points,
    )
