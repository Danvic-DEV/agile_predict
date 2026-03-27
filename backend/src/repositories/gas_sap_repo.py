"""Read repository for National Gas System Average Price (SAP) data."""

from __future__ import annotations

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.repositories.sql_models import GasSapORM


class GasSapRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_all_as_series(self) -> pd.Series:
        """Return all gas SAP rows as a UTC-indexed Series (date → gas_sap)."""
        rows = self.session.execute(select(GasSapORM).order_by(GasSapORM.date)).scalars().all()
        if not rows:
            return pd.Series(dtype=float, name="gas_sap")
        dates = pd.to_datetime([r.date for r in rows], utc=True).normalize()
        values = [r.gas_sap for r in rows]
        return pd.Series(values, index=dates, name="gas_sap")

    def get_latest(self) -> float | None:
        """Return the most recent gas SAP value, or None if no data."""
        row = (
            self.session.execute(
                select(GasSapORM).order_by(GasSapORM.date.desc()).limit(1)
            )
            .scalars()
            .first()
        )
        return float(row.gas_sap) if row is not None else None
