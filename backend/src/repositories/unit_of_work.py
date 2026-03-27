from __future__ import annotations

from sqlalchemy.orm import Session

from src.repositories.agile_actual_write_repo import AgileActualWriteRepository
from src.repositories.agile_data_repo import AgileDataRepository
from src.repositories.agile_data_write_repo import AgileDataWriteRepository
from src.repositories.forecast_data_repo import ForecastDataRepository
from src.repositories.forecast_data_write_repo import ForecastDataWriteRepository
from src.repositories.forecast_repo import ForecastRepository
from src.repositories.forecast_write_repo import ForecastWriteRepository
from src.repositories.external_system_context_write_repo import ExternalSystemContextWriteRepository
from src.repositories.gas_sap_repo import GasSapRepository
from src.repositories.gas_sap_write_repo import GasSapWriteRepository
from src.repositories.price_history_repo import PriceHistoryRepository
from src.repositories.price_history_write_repo import PriceHistoryWriteRepository


class UnitOfWork:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.forecasts = ForecastRepository(session)
        self.forecast_writes = ForecastWriteRepository(session)
        self.forecast_data = ForecastDataRepository(session)
        self.forecast_data_writes = ForecastDataWriteRepository(session)
        self.agile_data = AgileDataRepository(session)
        self.agile_data_writes = AgileDataWriteRepository(session)
        self.agile_actual_writes = AgileActualWriteRepository(session)
        self.price_history = PriceHistoryRepository(session)
        self.price_history_writes = PriceHistoryWriteRepository(session)
        self.external_system_context_writes = ExternalSystemContextWriteRepository(session)
        self.gas_sap = GasSapRepository(session)
        self.gas_sap_writes = GasSapWriteRepository(session)

    def commit(self) -> None:
        self.session.commit()

    def rollback(self) -> None:
        self.session.rollback()
