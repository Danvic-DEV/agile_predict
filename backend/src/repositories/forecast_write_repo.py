from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, select
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

    def list_older_than(self, cutoff: datetime) -> list[ForecastORM]:
        stmt = select(ForecastORM).where(ForecastORM.created_at < cutoff)
        return list(self.session.execute(stmt).scalars().all())

    def delete_by_ids(self, ids: list[int]) -> int:
        if not ids:
            return 0
        stmt = delete(ForecastORM).where(ForecastORM.id.in_(ids))
        result = self.session.execute(stmt)
        return int(result.rowcount or 0)
