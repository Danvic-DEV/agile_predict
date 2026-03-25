from __future__ import annotations

from sqlalchemy import delete, update
from sqlalchemy.orm import Session

from src.repositories.sql_models import AgileActualORM, AgileDataORM
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

    def delete_for_forecasts(self, forecast_ids: list[int]) -> int:
        if not forecast_ids:
            return 0
        stmt = delete(AgileDataORM).where(AgileDataORM.forecast_id.in_(forecast_ids))
        result = self.session.execute(stmt)
        return int(result.rowcount or 0)

    def patch_pred_from_actuals(self, forecast_ids: list[int]) -> int:
        """Overwrite agile_pred (and low/high) with the published Octopus price
        for any forecast slot where an actual price is already known.

        This means that once Octopus publishes tomorrow's Agile tariff (~16:00 UK
        time), the forecast for those slots immediately reflects the real price
        rather than the Nordpool-derived transform.
        """
        if not forecast_ids:
            return 0

        # Subquery: for each (date_time, region) find the published actual price
        actual_sub = (
            self.session.query(AgileActualORM.date_time, AgileActualORM.region, AgileActualORM.agile_actual)
            .subquery()
        )

        stmt = (
            update(AgileDataORM)
            .where(AgileDataORM.forecast_id.in_(forecast_ids))
            .where(AgileDataORM.date_time == actual_sub.c.date_time)
            .where(AgileDataORM.region == actual_sub.c.region)
            .values(
                agile_pred=actual_sub.c.agile_actual,
                agile_low=actual_sub.c.agile_actual,
                agile_high=actual_sub.c.agile_actual,
            )
            .execution_options(synchronize_session=False)
        )
        result = self.session.execute(stmt)
        return int(result.rowcount or 0)
