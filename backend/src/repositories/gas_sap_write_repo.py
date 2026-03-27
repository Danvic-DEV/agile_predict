"""Write repository for National Gas System Average Price (SAP) data."""

from __future__ import annotations

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from src.repositories.sql_models import GasSapORM

_UPSERT_BATCH_SIZE = 5000


class GasSapWriteRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def upsert_many(self, rows: list[dict]) -> int:
        """Insert or update GasSap rows keyed on date.

        Each dict must have keys: date (datetime, UTC midnight, tz-aware), gas_sap (float).
        """
        if not rows:
            return 0

        total_rowcount = 0
        for start in range(0, len(rows), _UPSERT_BATCH_SIZE):
            batch = rows[start : start + _UPSERT_BATCH_SIZE]
            stmt = (
                insert(GasSapORM)
                .values(batch)
                .on_conflict_do_update(
                    index_elements=["date"],
                    set_={"gas_sap": insert(GasSapORM).excluded.gas_sap},
                )
            )
            result = self.session.execute(stmt)
            # rowcount can be -1 for ON CONFLICT DO UPDATE in PostgreSQL;
            # fall back to batch size for accurate accounting.
            rc = result.rowcount
            total_rowcount += len(batch) if rc < 0 else int(rc)

        self.session.flush()
        return total_rowcount
