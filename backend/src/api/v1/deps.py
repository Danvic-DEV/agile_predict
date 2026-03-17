from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from src.core.db import get_db_session
from src.repositories.forecast_repo import ForecastRepository
from src.repositories.unit_of_work import UnitOfWork

DbSession = Annotated[Session, Depends(get_db_session)]


def get_forecast_repository(session: DbSession) -> ForecastRepository:
    return ForecastRepository(session=session)


ForecastRepositoryDep = Annotated[ForecastRepository, Depends(get_forecast_repository)]


def get_uow(session: DbSession) -> UnitOfWork:
    return UnitOfWork(session=session)


UnitOfWorkDep = Annotated[UnitOfWork, Depends(get_uow)]
