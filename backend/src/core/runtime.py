from __future__ import annotations

import asyncio
import logging
from sqlalchemy import select

from src.core.discord_notifications import send_update_failure_notification
from src.core.db import SessionLocal, engine
from src.core.settings import settings
from src.domain.bootstrap_bundle import BootstrapBundleConfig, write_bootstrap_bundle
from src.jobs.pipelines.update_forecast import run_update_forecast_job
from src.repositories.sql_models import Base, ForecastORM
from src.repositories.unit_of_work import UnitOfWork

logger = logging.getLogger(__name__)


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
        raise RuntimeError("update forecast job completed but wrote zero records")
    except Exception:
        uow.rollback()
        if not settings.allow_startup_bootstrap_fallback:
            raise

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


def _run_update_job_once() -> None:
    session = SessionLocal()
    try:
        uow = UnitOfWork(session=session)
        run_update_forecast_job(uow=uow)
        uow.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


class AutoUpdateScheduler:
    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None
        self._stopped = asyncio.Event()
        self._run_lock = asyncio.Lock()

    async def start(self) -> None:
        if not settings.auto_update_enabled:
            logger.info("Auto update scheduler is disabled.")
            return

        if self._task is not None and not self._task.done():
            return

        self._stopped.clear()
        self._task = asyncio.create_task(self._run_loop(), name="auto-update-scheduler")

    async def stop(self) -> None:
        self._stopped.set()
        if self._task is None:
            return

        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        finally:
            self._task = None

    async def _run_loop(self) -> None:
        interval = settings.auto_update_interval_seconds
        logger.info("Auto update scheduler started (interval=%ss).", interval)

        if settings.auto_update_run_immediately:
            await self._run_once_safe()

        while not self._stopped.is_set():
            try:
                await asyncio.wait_for(self._stopped.wait(), timeout=interval)
                break
            except asyncio.TimeoutError:
                await self._run_once_safe()

        logger.info("Auto update scheduler stopped.")

    async def _run_once_safe(self) -> None:
        if self._run_lock.locked():
            logger.warning("Skipping auto update run: previous run still in progress.")
            return

        async with self._run_lock:
            try:
                await asyncio.to_thread(_run_update_job_once)
                logger.info("Auto update run completed successfully.")
            except Exception as exc:
                send_update_failure_notification(detail=str(exc), trigger="auto")
                logger.exception("Auto update run failed: %s", exc)
