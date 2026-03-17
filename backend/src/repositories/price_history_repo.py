from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.repositories.sql_models import PriceHistoryORM


class PriceHistoryRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def latest_date_time(self):
        stmt = select(PriceHistoryORM.date_time).order_by(PriceHistoryORM.date_time.desc()).limit(1)
        return self.session.execute(stmt).scalar_one_or_none()
