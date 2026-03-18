from __future__ import annotations

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from src.repositories.sql_models import ExternalSystemContextORM


class ExternalSystemContextWriteRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def upsert_many(self, rows: list[dict]) -> int:
        if not rows:
            return 0

        stmt = (
            insert(ExternalSystemContextORM)
            .values(rows)
            .on_conflict_do_update(
                index_elements=["date_time"],
                set_={
                    "carbon_intensity": insert(ExternalSystemContextORM).excluded.carbon_intensity,
                    "gas_mw": insert(ExternalSystemContextORM).excluded.gas_mw,
                    "wind_mw": insert(ExternalSystemContextORM).excluded.wind_mw,
                    "nuclear_mw": insert(ExternalSystemContextORM).excluded.nuclear_mw,
                    "pumped_storage_mw": insert(ExternalSystemContextORM).excluded.pumped_storage_mw,
                    "interconnector_net_mw": insert(ExternalSystemContextORM).excluded.interconnector_net_mw,
                },
            )
        )
        result = self.session.execute(stmt)
        self.session.flush()
        rowcount = getattr(result, "rowcount", None)
        return int(rowcount or 0)
