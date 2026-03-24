from __future__ import annotations

import asyncio
import logging
from sqlalchemy import select

from src.core.discord_notifications import send_update_failure_notification
from src.core.db import SessionLocal, engine
from src.core.settings import settings
from src.domain.bootstrap_bundle import BootstrapBundleConfig, write_bootstrap_bundle
from src.jobs.pipelines.backfill_historical import run_backfill_job
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

    if settings.auto_bootstrap_mode == "update" and settings.allow_startup_bootstrap_fallback:
        logger.warning(
            "Skipping blocking startup update because ALLOW_STARTUP_BOOTSTRAP_FALLBACK=true; "
            "writing bootstrap seed and deferring update to scheduler."
        )
        _write_bootstrap_seed(uow)
        return "bootstrap-fallback"

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

    session = SessionLocal()
    try:
        existing_forecast = session.execute(select(ForecastORM.id).limit(1)).scalar_one_or_none()
        
        # Handle database seeding if needed
        if existing_forecast is None and settings.auto_bootstrap_on_startup:
            uow = UnitOfWork(session=session)
            seed_empty_database(uow=uow)
            uow.commit()
        
        # Run backfill (independent of bootstrap status)
        if settings.auto_backfill_on_startup:
            _run_initial_backfill_safe(session)
            
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _run_initial_backfill_safe(session) -> None:
    """Run backfill on startup for all configured regions."""
    try:
        regions = [r.strip().upper() for r in settings.auto_backfill_regions.split(",") if r.strip()]
        logger.info(f"Running initial historical backfill for regions: {', '.join(regions)}")
        
        for region in regions:
            try:
                uow = UnitOfWork(session=session)
                result = run_backfill_job(uow=uow, region=region)
                logger.info(
                    f"Initial backfill completed for region {region}: "
                    f"{result['forecasts_created']} forecasts, {result['data_rows_created']} data rows"
                )
            except Exception as exc:
                logger.warning(f"Initial backfill failed for region {region}: {exc}")
                # Continue with other regions even if one fails
                
    except Exception as exc:
        logger.warning(f"Initial backfill setup failed: {exc}")


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


def _run_backfill_job_once() -> dict:
    """Run backfill job for all configured regions."""
    session = SessionLocal()
    results = {}
    try:
        regions = [r.strip().upper() for r in settings.auto_backfill_regions.split(",") if r.strip()]
        
        for region in regions:
            try:
                uow = UnitOfWork(session=session)
                result = run_backfill_job(uow=uow, region=region)
                results[region] = result
                logger.info(
                    f"Backfill completed for region {region}: "
                    f"{result['forecasts_created']} new forecasts, {result['data_rows_created']} data rows"
                )
            except Exception as exc:
                logger.warning(f"Backfill failed for region {region}: {exc}")
                results[region] = {"status": "failed", "error": str(exc)}
                
    finally:
        session.close()
    
    return results


class AutoBackfillScheduler:
    """Periodic scheduler for historical weather backfill to extend training data."""
    
    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None
        self._stopped = asyncio.Event()
        self._run_lock = asyncio.Lock()

    async def start(self) -> None:
        if not settings.auto_backfill_enabled:
            logger.info("Auto backfill scheduler is disabled.")
            return

        if self._task is not None and not self._task.done():
            return

        self._stopped.clear()
        self._task = asyncio.create_task(self._run_loop(), name="auto-backfill-scheduler")

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
        interval = settings.auto_backfill_check_interval_seconds
        logger.info("Auto backfill scheduler started (interval=%ss).", interval)

        while not self._stopped.is_set():
            try:
                await asyncio.wait_for(self._stopped.wait(), timeout=interval)
                break
            except asyncio.TimeoutError:
                await self._run_once_safe()

        logger.info("Auto backfill scheduler stopped.")

    async def _run_once_safe(self) -> None:
        if self._run_lock.locked():
            logger.warning("Skipping auto backfill run: previous run still in progress.")
            return

        async with self._run_lock:
            try:
                await asyncio.to_thread(_run_backfill_job_once)
                logger.info("Auto backfill run completed successfully.")
            except Exception as exc:
                logger.exception("Auto backfill run failed: %s", exc)
