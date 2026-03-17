from __future__ import annotations

from sqlalchemy import delete
from sqlalchemy.orm import Session

from src.repositories.sql_models import ForecastDataORM
from src.repositories.types import ForecastDataWrite


class ForecastDataWriteRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def bulk_insert(self, rows: list[ForecastDataWrite]) -> int:
        if not rows:
            return 0

        db_rows = [
            ForecastDataORM(
                forecast_id=row.forecast_id,
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
        self.session.add_all(db_rows)
        self.session.flush()
        return len(db_rows)

    def delete_for_forecast(self, forecast_id: int) -> int:
        stmt = delete(ForecastDataORM).where(ForecastDataORM.forecast_id == forecast_id)
        result = self.session.execute(stmt)
        return int(result.rowcount or 0)
