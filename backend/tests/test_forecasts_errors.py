from __future__ import annotations

import pytest

from src.api.v1.routes import forecasts as forecast_routes


class _ForecastDataRepo:
    def list_for_forecast(self, forecast_id: int, limit: int = 336):
        del forecast_id, limit
        return []


class _FakeUow:
    forecast_data = _ForecastDataRepo()


def test_forecast_data_stats_not_found_returns_structured_404() -> None:
    with pytest.raises(Exception) as exc_info:
        forecast_routes.get_forecast_data_stats(forecast_id=123, uow=_FakeUow(), limit=20)

    err = exc_info.value
    assert getattr(err, "status_code", None) == 404
    assert err.detail["code"] == "forecast_data_not_found"
    assert err.detail["message"] == "No forecast data rows found for forecast_id."
