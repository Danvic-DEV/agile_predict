from __future__ import annotations

from typing import Any, cast

from src.domain.forecast_pipeline import ForecastPipelineOutput
from src.jobs.pipelines import update_forecast


class _FakeResult:
    def __init__(self, forecast_data_points_written: int, agile_data_points_written: int) -> None:
        self.forecast_name = "bundle::update-job-seed"
        self.forecast_data_points_written = forecast_data_points_written
        self.agile_data_points_written = agile_data_points_written


class _FakeUow:
    pass


def test_run_update_forecast_job_passes_day_ahead_values_into_bundle(monkeypatch) -> None:
    uow = _FakeUow()

    monkeypatch.setattr(
        update_forecast,
        "run_forecast_pipeline",
        lambda **kwargs: ForecastPipelineOutput(
            day_ahead_values=(70.0, 71.0, 72.0),
            source="nordpool",
            agile_preview_mean=18.0,
        ),
    )
    state_call: dict[str, Any] = {}
    monkeypatch.setattr(update_forecast, "write_last_update_job_state", lambda **kwargs: state_call.update(kwargs))

    captured: dict[str, Any] = {}

    def _fake_write_bootstrap_bundle(*, uow: Any, config: Any) -> _FakeResult:
        captured["config"] = config
        return _FakeResult(forecast_data_points_written=3, agile_data_points_written=6)

    monkeypatch.setattr(update_forecast, "write_bootstrap_bundle", _fake_write_bootstrap_bundle)

    result = update_forecast.run_update_forecast_job(uow=cast(Any, uow))

    config = captured["config"]
    assert config.points == 3
    assert config.day_ahead_values == (70.0, 71.0, 72.0)
    assert result.records_written == 9
    assert result.forecast_name == "bundle::update-job-seed"
    assert result.source == "nordpool"
    assert result.day_ahead_points == 3
    assert state_call["source"] == "nordpool"
    assert state_call["raw_points"] == 0
    assert state_call["aligned_points"] == 0
    assert state_call["interpolated_points"] == 0
    assert state_call["retries_used"] == 0
