from __future__ import annotations

from sqlalchemy import select

from src.core.db import SessionLocal, engine
from src.core.settings import settings
from src.domain.bootstrap_bundle import BootstrapBundleConfig, write_bootstrap_bundle
from src.jobs.pipelines.update_forecast import run_update_forecast_job
from src.repositories.sql_models import Base, ForecastORM
from src.repositories.unit_of_work import UnitOfWork


def _write_bootstrap_seed(uow: UnitOfWork) -> None:
    write_bootstrap_bundle(
        uow=uow,
        config=BootstrapBundleConfig(
            points=settings.auto_bootstrap_points,
            idempotency_key="startup-seed",
            replace_existing=True,
            regions=tuple(settings.bootstrap_regions_list),
            write_agile_data=True,
        ),
    )


def seed_empty_database(uow: UnitOfWork) -> str:
    if settings.auto_bootstrap_mode == "bootstrap":
        _write_bootstrap_seed(uow)
        return "bootstrap"

    try:
        result = run_update_forecast_job(uow=uow)
        if result.records_written > 0:
            return "update"
    except Exception:
        uow.rollback()

    _write_bootstrap_seed(uow)
    return "bootstrap-fallback"


def initialize_runtime() -> None:
    Base.metadata.create_all(bind=engine)

    if not settings.auto_bootstrap_on_startup:
        return

    session = SessionLocal()
    try:
        existing_forecast = session.execute(select(ForecastORM.id).limit(1)).scalar_one_or_none()
        if existing_forecast is not None:
            return

        uow = UnitOfWork(session=session)
        seed_empty_database(uow=uow)
        uow.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
