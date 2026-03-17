from __future__ import annotations

from sqlalchemy import delete
from sqlalchemy.orm import Session

from src.repositories.sql_models import AgileDataORM
from src.repositories.types import AgileDataWrite


class AgileDataWriteRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def bulk_insert(self, rows: list[AgileDataWrite]) -> int:
        if not rows:
            return 0

        db_rows = [
            AgileDataORM(
                forecast_id=row.forecast_id,
                region=row.region,
                agile_pred=row.agile_pred,
                agile_low=row.agile_low,
                agile_high=row.agile_high,
                date_time=row.date_time,
            )
            for row in rows
        ]
        self.session.add_all(db_rows)
        self.session.flush()
        return len(db_rows)

    def delete_for_forecast(self, forecast_id: int) -> int:
        stmt = delete(AgileDataORM).where(AgileDataORM.forecast_id == forecast_id)
        result = self.session.execute(stmt)
        return int(result.rowcount or 0)
