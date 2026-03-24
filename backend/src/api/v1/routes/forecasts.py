from fastapi import APIRouter, HTTPException, Query

from src.api.v1.deps import UnitOfWorkDep, ForecastRepositoryDep
from src.api.errors import http_error
from src.core.update_job_state import read_last_update_job_state
from src.core.regions import REGION_FACTORS
from src.schemas.forecast import ForecastDataPoint, ForecastSummary, ForecastWithPrices

router = APIRouter()


def _ensure_customer_forecast_is_trusted() -> None:
    state = read_last_update_job_state() or {}
    source = state.get("source") or ""
    if (
        (source == "ml" or source.startswith("ml:"))
        and state.get("ml_write_mode") == "ml"
        and not bool(state.get("training_mode", False))
    ):
        return

    raise http_error(
        503,
        "customer_forecast_unavailable",
        "Customer-facing forecast output is disabled until a trusted ML forecast is available.",
    )


@router.get("", response_model=list[ForecastSummary])
def list_forecasts(
    repo: ForecastRepositoryDep,
    limit: int = Query(default=1, ge=1, le=30),
) -> list[ForecastSummary]:
    _ensure_customer_forecast_is_trusted()
    return repo.list_latest(limit=limit)


@router.get("/prices", response_model=list[ForecastWithPrices])
def list_forecasts_with_prices(
    repo: ForecastRepositoryDep,
    region: str | None = Query(default=None, min_length=1, max_length=1),
    days: int = Query(default=14, ge=1, le=30),
    forecast_count: int = Query(default=1, ge=1, le=30),
    high_low: bool = Query(default=True),
) -> list[ForecastWithPrices]:
    _ensure_customer_forecast_is_trusted()
    rows = repo.list_with_prices(
        region=region,
        days=days,
        forecast_count=forecast_count,
        include_high_low=high_low,
    )
    if not rows:
        raise http_error(
            503,
            "no_operational_forecast_available",
            "No operational forecast is currently available for customer-facing prices.",
        )
    return rows


@router.get("/{forecast_id}/data", response_model=list[ForecastDataPoint])
def list_forecast_data(
    forecast_id: int,
    uow: UnitOfWorkDep,
    limit: int = Query(default=336, ge=1, le=2000),
) -> list[ForecastDataPoint]:
    return uow.forecast_data.list_for_forecast(forecast_id=forecast_id, limit=limit)


@router.get("/{forecast_id}/data-stats")
def get_forecast_data_stats(
    forecast_id: int,
    uow: UnitOfWorkDep,
    limit: int = Query(default=336, ge=1, le=2000),
) -> dict[str, float | int | str | None]:
    rows = uow.forecast_data.list_for_forecast(forecast_id=forecast_id, limit=limit)
    if not rows:
        raise http_error(404, "forecast_data_not_found", "No forecast data rows found for forecast_id.")

    day_ahead_values = [r.day_ahead for r in rows if r.day_ahead is not None]
    stats: dict[str, float | int | str | None] = {
        "forecast_id": forecast_id,
        "count": len(rows),
        "first_date_time": rows[0].date_time.isoformat(),
        "last_date_time": rows[-1].date_time.isoformat(),
        "day_ahead_min": min(day_ahead_values) if day_ahead_values else None,
        "day_ahead_max": max(day_ahead_values) if day_ahead_values else None,
        "day_ahead_mean": round(sum(day_ahead_values) / len(day_ahead_values), 6) if day_ahead_values else None,
        "demand_mean": round(sum(r.demand for r in rows) / len(rows), 6),
    }
    return stats


@router.get("/regions", response_model=list[str])
def list_regions() -> list[str]:
    return sorted(REGION_FACTORS.keys())
