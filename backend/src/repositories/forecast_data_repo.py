from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.repositories.sql_models import ForecastDataORM
from src.schemas.forecast import ForecastDataPoint


class ForecastDataRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_for_forecast(self, forecast_id: int, limit: int = 336) -> list[ForecastDataPoint]:
        stmt = (
            select(ForecastDataORM)
            .where(ForecastDataORM.forecast_id == forecast_id)
            .order_by(ForecastDataORM.date_time.asc())
            .limit(limit)
        )
        rows = self.session.execute(stmt).scalars().all()
        return [
            ForecastDataPoint(
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
            for row in rows
        ]
