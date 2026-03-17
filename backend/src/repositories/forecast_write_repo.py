from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.repositories.sql_models import ForecastORM


class ForecastWriteRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_by_name(self, name: str) -> ForecastORM | None:
        stmt = select(ForecastORM).where(ForecastORM.name == name)
        return self.session.execute(stmt).scalar_one_or_none()

    def create_forecast(
        self,
        name: str,
        created_at: datetime,
        mean: float | None = None,
        stdev: float | None = None,
    ) -> ForecastORM:
        row = ForecastORM(name=name, created_at=created_at, mean=mean, stdev=stdev)
        self.session.add(row)
        self.session.flush()
        return row
