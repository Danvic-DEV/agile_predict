from fastapi import APIRouter

from src.api.v1.routes import admin_jobs, diagnostics, forecasts, health

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(forecasts.router, prefix="/forecasts", tags=["forecasts"])
api_router.include_router(diagnostics.router, prefix="/diagnostics", tags=["diagnostics"])
api_router.include_router(admin_jobs.router, prefix="/admin-jobs", tags=["admin-jobs"])
