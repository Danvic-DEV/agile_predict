from __future__ import annotations

from sqlalchemy.orm import Session

from src.repositories.agile_data_repo import AgileDataRepository
from src.repositories.agile_data_write_repo import AgileDataWriteRepository
from src.repositories.forecast_data_repo import ForecastDataRepository
from src.repositories.forecast_data_write_repo import ForecastDataWriteRepository
from src.repositories.forecast_repo import ForecastRepository
from src.repositories.forecast_write_repo import ForecastWriteRepository
from src.repositories.price_history_repo import PriceHistoryRepository


class UnitOfWork:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.forecasts = ForecastRepository(session)
        self.forecast_writes = ForecastWriteRepository(session)
        self.forecast_data = ForecastDataRepository(session)
        self.forecast_data_writes = ForecastDataWriteRepository(session)
        self.agile_data = AgileDataRepository(session)
        self.agile_data_writes = AgileDataWriteRepository(session)
        self.price_history = PriceHistoryRepository(session)

    def commit(self) -> None:
        self.session.commit()

    def rollback(self) -> None:
        self.session.rollback()
