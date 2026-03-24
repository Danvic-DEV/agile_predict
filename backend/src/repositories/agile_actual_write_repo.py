"""Write repository for actual released Agile prices."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from src.repositories.sql_models import AgileActualORM


_UPSERT_BATCH_SIZE = 5000


class AgileActualWriteRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def upsert_many(self, rows: list[dict]) -> int:
        """Insert or update AgileActual rows keyed on (date_time, region).

        Each dict must have keys: date_time (datetime, tz-aware), region (str), agile_actual (float).
        """
        if not rows:
            return 0

        total_rowcount = 0
        for start in range(0, len(rows), _UPSERT_BATCH_SIZE):
            batch = rows[start : start + _UPSERT_BATCH_SIZE]
            stmt = (
                insert(AgileActualORM)
                .values(batch)
                .on_conflict_do_update(
                    index_elements=["date_time", "region"],
                    set_={"agile_actual": insert(AgileActualORM).excluded.agile_actual},
                )
            )
            result = self.session.execute(stmt)
            total_rowcount += int(result.rowcount or 0)

        self.session.flush()
        return total_rowcount
