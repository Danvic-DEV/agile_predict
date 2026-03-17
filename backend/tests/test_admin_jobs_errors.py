from __future__ import annotations

from dataclasses import dataclass

from fastapi.testclient import TestClient

import src.main as main_module
from src.api.v1.routes import admin_jobs
from src.api.v1.deps import get_uow


@dataclass
class _FakeResult:
    records_written: int
    forecast_name: str
    source: str | None = None
    day_ahead_points: int | None = None


class _FakeUow:
    def __init__(self) -> None:
        self.rolled_back = False

    def rollback(self) -> None:
        self.rolled_back = True

    def commit(self) -> None:
        return None


def _client_without_runtime(monkeypatch) -> TestClient:
    monkeypatch.setattr(main_module, "initialize_runtime", lambda: None)
    app = main_module.create_app()
    app.dependency_overrides[get_uow] = lambda: _FakeUow()
    return TestClient(app)


def test_run_update_forecast_job_error_contract(monkeypatch) -> None:
    client = _client_without_runtime(monkeypatch)

    def _raise_update(*args, **kwargs):
        raise RuntimeError("ML forecast failed with fallback disabled: synthetic")

    monkeypatch.setattr(admin_jobs, "run_update_forecast_job", _raise_update)

    response = client.post("/api/v1/admin-jobs/run-update-forecast-job")

    assert response.status_code == 400
    payload = response.json()
    assert "detail" in payload
    detail = payload["detail"]
    assert detail["code"] == "update_forecast_job_failed"
    assert detail["message"] == "Update forecast job failed."
    assert detail["error_type"] == "RuntimeError"
    assert "fallback disabled" in detail["error"]


def test_bootstrap_forecast_error_contract(monkeypatch) -> None:
    client = _client_without_runtime(monkeypatch)

    payload = {
        "points": 4,
        "regions": ["ZZ"],
        "spread": 1.5,
        "base_price": 20.0,
        "replace_existing": True,
        "idempotency_key": None,
    }

    response = client.post("/api/v1/admin-jobs/bootstrap-forecast", json=payload)

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["code"] == "bootstrap_forecast_failed"
    assert detail["message"] == "Bootstrap forecast failed."
    assert detail["error_type"] == "ValueError"
    assert "Unsupported region" in detail["error"]
