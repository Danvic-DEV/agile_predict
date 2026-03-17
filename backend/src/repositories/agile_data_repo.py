from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.repositories.sql_models import AgileDataORM


class AgileDataRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_for_forecast(self, forecast_id: int, region: str | None = None) -> list[AgileDataORM]:
        stmt = select(AgileDataORM).where(AgileDataORM.forecast_id == forecast_id)
        if region is not None:
            stmt = stmt.where(AgileDataORM.region == region.upper())
        return self.session.execute(stmt).scalars().all()
