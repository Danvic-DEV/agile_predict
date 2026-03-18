from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from src.repositories.sql_models import PriceHistoryORM


class PriceHistoryWriteRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def upsert_many(self, rows: list[dict]) -> int:
        """Insert or update PriceHistory rows keyed on date_time.

        Each dict must have keys: date_time (datetime, tz-aware), day_ahead (float), agile (float).
        """
        if not rows:
            return 0

        stmt = (
            insert(PriceHistoryORM)
            .values(rows)
            .on_conflict_do_update(
                index_elements=["date_time"],
                set_={"day_ahead": insert(PriceHistoryORM).excluded.day_ahead},
            )
        )
        result = self.session.execute(stmt)
        self.session.flush()
        return int(result.rowcount or 0)

    def delete_older_than(self, cutoff: datetime) -> int:
        stmt = delete(PriceHistoryORM).where(PriceHistoryORM.date_time < cutoff)
        result = self.session.execute(stmt)
        return int(result.rowcount or 0)
