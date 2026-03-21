from __future__ import annotations

import pytest

from src.api.v1.routes import forecasts as forecast_routes


class _ForecastDataRepo:
    def list_for_forecast(self, forecast_id: int, limit: int = 336):
        del forecast_id, limit
        return []


class _FakeUow:
    forecast_data = _ForecastDataRepo()


class _ForecastRepo:
    def __init__(self, rows=None):
        self._rows = rows or []

    def list_latest(self, limit: int = 1):
        del limit
        return self._rows

    def list_with_prices(self, *, region=None, days=14, forecast_count=1, include_high_low=True):
        del region, days, forecast_count, include_high_low
        return self._rows


def test_forecast_data_stats_not_found_returns_structured_404() -> None:
    with pytest.raises(Exception) as exc_info:
        forecast_routes.get_forecast_data_stats(forecast_id=123, uow=_FakeUow(), limit=20)

    err = exc_info.value
    assert getattr(err, "status_code", None) == 404
    assert err.detail["code"] == "forecast_data_not_found"
    assert err.detail["message"] == "No forecast data rows found for forecast_id."


def test_list_forecasts_rejects_deterministic_customer_output(monkeypatch) -> None:
    monkeypatch.setattr(
        forecast_routes,
        "read_last_update_job_state",
        lambda: {"source": "nordpool", "ml_write_mode": "deterministic", "training_mode": True},
    )

    with pytest.raises(Exception) as exc_info:
        forecast_routes.list_forecasts(repo=_ForecastRepo(), limit=1)

    err = exc_info.value
    assert getattr(err, "status_code", None) == 503
    assert err.detail["code"] == "customer_forecast_unavailable"


def test_list_forecasts_with_prices_requires_trusted_ml_output(monkeypatch) -> None:
    monkeypatch.setattr(
        forecast_routes,
        "read_last_update_job_state",
        lambda: {"source": "nordpool", "ml_write_mode": "deterministic", "training_mode": True},
    )

    with pytest.raises(Exception) as exc_info:
        forecast_routes.list_forecasts_with_prices(repo=_ForecastRepo(), region="G", days=14, forecast_count=1, high_low=True)

    err = exc_info.value
    assert getattr(err, "status_code", None) == 503
    assert err.detail["code"] == "customer_forecast_unavailable"


def test_list_forecasts_with_prices_allows_trusted_ml_output(monkeypatch) -> None:
    rows = [object()]
    monkeypatch.setattr(
        forecast_routes,
        "read_last_update_job_state",
        lambda: {"source": "ml", "ml_write_mode": "ml", "training_mode": False},
    )

    result = forecast_routes.list_forecasts_with_prices(
        repo=_ForecastRepo(rows=rows),
        region="G",
        days=14,
        forecast_count=1,
        high_low=True,
    )

    assert result == rows
