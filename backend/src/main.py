from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from src.api.v1.router import api_router
from src.core.runtime import AutoBackfillScheduler, AutoUpdateScheduler, initialize_runtime
from src.core.settings import settings

FRONTEND_DIST_DIR = Path("/app/frontend-dist")


@asynccontextmanager
async def lifespan(app: FastAPI):
    initialize_runtime()
    
    update_scheduler = AutoUpdateScheduler()
    await update_scheduler.start()
    app.state.auto_update_scheduler = update_scheduler
    
    backfill_scheduler = AutoBackfillScheduler()
    await backfill_scheduler.start()
    app.state.auto_backfill_scheduler = backfill_scheduler
    
    try:
        yield
    finally:
        await update_scheduler.stop()
        await backfill_scheduler.stop()


def create_app() -> FastAPI:
    app = FastAPI(title=settings.app_name, version=settings.app_version, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(api_router, prefix="/api/v1")

    @app.get("/healthz", tags=["health"])
    def healthz() -> dict[str, str]:
        return {"status": "ok", "service": settings.app_name}

    if FRONTEND_DIST_DIR.exists():
        assets_dir = FRONTEND_DIST_DIR / "assets"
        if assets_dir.exists():
            app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        def serve_frontend(full_path: str) -> FileResponse:
            candidate = FRONTEND_DIST_DIR / full_path
            if full_path and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(FRONTEND_DIST_DIR / "index.html")

    return app


app = create_app()
