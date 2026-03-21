"""Write repository for actual released Agile prices."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from src.repositories.sql_models import AgileActualORM


class AgileActualWriteRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def upsert_many(self, rows: list[dict]) -> int:
        """Insert or update AgileActual rows keyed on (date_time, region).

        Each dict must have keys: date_time (datetime, tz-aware), region (str), agile_actual (float).
        """
        if not rows:
            return 0

        stmt = (
            insert(AgileActualORM)
            .values(rows)
            .on_conflict_do_update(
                index_elements=["date_time", "region"],
                set_={"agile_actual": insert(AgileActualORM).excluded.agile_actual},
            )
        )
        result = self.session.execute(stmt)
        self.session.flush()
        return int(result.rowcount or 0)
