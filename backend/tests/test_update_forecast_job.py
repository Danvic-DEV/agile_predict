from __future__ import annotations

from typing import Any, cast
import pytest

from src.domain.forecast_pipeline import ForecastPipelineOutput
from src.ml.parity.day_ahead_xgb import MlParityForecastOutput
from src.jobs.pipelines import update_forecast


class _FakeResult:
    def __init__(self, forecast_data_points_written: int, agile_data_points_written: int) -> None:
        self.forecast_name = "bundle::update-job-seed"
        self.forecast_data_points_written = forecast_data_points_written
        self.agile_data_points_written = agile_data_points_written


class _FakeUow:
    pass


@pytest.fixture(autouse=True)
def _stub_ml_readiness(monkeypatch) -> None:
    monkeypatch.setattr(update_forecast, "check_ml_training_readiness", lambda **kwargs: (False, None))


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
    monkeypatch.setattr(update_forecast.settings, "ml_write_mode", "deterministic")

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


def test_run_update_forecast_job_raises_when_ml_fails_and_fallback_disabled(monkeypatch) -> None:
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
    monkeypatch.setattr(update_forecast, "run_ml_day_ahead_forecast", lambda **kwargs: (_ for _ in ()).throw(ValueError("no ml")))
    monkeypatch.setattr(update_forecast.settings, "ml_write_mode", "ml")
    monkeypatch.setattr(update_forecast.settings, "allow_ml_fallback", False)

    with pytest.raises(RuntimeError):
        update_forecast.run_update_forecast_job(uow=cast(Any, uow))


def test_run_update_forecast_job_prefers_ml_output_when_available(monkeypatch) -> None:
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
    monkeypatch.setattr(
        update_forecast,
        "run_ml_day_ahead_forecast",
        lambda **kwargs: MlParityForecastOutput(
            day_ahead_values=(80.0, 81.0, 82.0),
            day_ahead_low_values=(78.0, 79.0, 80.0),
            day_ahead_high_values=(82.0, 83.0, 84.0),
            cv_mean_rmse=3.2,
            cv_stdev_rmse=0.4,
            training_rows=120,
            test_rows=40,
            feature_columns=("bm_wind", "solar"),
            range_mode="kde",
        ),
    )
    monkeypatch.setattr(update_forecast.settings, "ml_write_mode", "ml")

    state_call: dict[str, Any] = {}
    monkeypatch.setattr(update_forecast, "write_last_update_job_state", lambda **kwargs: state_call.update(kwargs))

    captured: dict[str, Any] = {}

    def _fake_write_bootstrap_bundle(*, uow: Any, config: Any) -> _FakeResult:
        captured["config"] = config
        return _FakeResult(forecast_data_points_written=3, agile_data_points_written=6)

    monkeypatch.setattr(update_forecast, "write_bootstrap_bundle", _fake_write_bootstrap_bundle)

    result = update_forecast.run_update_forecast_job(uow=cast(Any, uow))

    config = captured["config"]
    assert config.day_ahead_values == (80.0, 81.0, 82.0)
    assert config.day_ahead_low_values == (78.0, 79.0, 80.0)
    assert config.day_ahead_high_values == (82.0, 83.0, 84.0)
    assert config.forecast_mean == 3.2
    assert config.forecast_stdev == 0.4
    assert result.source == "ml"
    assert state_call["ml_training_rows"] == 120
    assert state_call["ml_range_mode"] == "kde"


def test_run_update_forecast_job_shadow_mode_writes_deterministic(monkeypatch) -> None:
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
    monkeypatch.setattr(
        update_forecast,
        "run_ml_day_ahead_forecast",
        lambda **kwargs: MlParityForecastOutput(
            day_ahead_values=(80.0, 81.0, 82.0),
            day_ahead_low_values=(78.0, 79.0, 80.0),
            day_ahead_high_values=(82.0, 83.0, 84.0),
            cv_mean_rmse=3.2,
            cv_stdev_rmse=0.4,
            training_rows=120,
            test_rows=40,
            feature_columns=("bm_wind", "solar"),
            range_mode="kde",
        ),
    )
    monkeypatch.setattr(update_forecast.settings, "ml_write_mode", "shadow")

    state_call: dict[str, Any] = {}
    monkeypatch.setattr(update_forecast, "write_last_update_job_state", lambda **kwargs: state_call.update(kwargs))

    captured: dict[str, Any] = {}

    def _fake_write_bootstrap_bundle(*, uow: Any, config: Any) -> _FakeResult:
        captured["config"] = config
        return _FakeResult(forecast_data_points_written=3, agile_data_points_written=6)

    monkeypatch.setattr(update_forecast, "write_bootstrap_bundle", _fake_write_bootstrap_bundle)

    result = update_forecast.run_update_forecast_job(uow=cast(Any, uow))

    config = captured["config"]
    assert config.day_ahead_values == (70.0, 71.0, 72.0)
    assert config.day_ahead_low_values is None
    assert config.day_ahead_high_values is None
    assert result.source == "shadow:nordpool"
    assert state_call["ml_compare_mae"] is not None
    assert state_call["ml_write_mode"] == "shadow"


def test_run_update_forecast_job_rejects_degenerate_ml_output(monkeypatch) -> None:
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
    monkeypatch.setattr(
        update_forecast,
        "run_ml_day_ahead_forecast",
        lambda **kwargs: MlParityForecastOutput(
            day_ahead_values=(0.0, 0.0, 0.0),
            day_ahead_low_values=(0.0, 0.0, 0.0),
            day_ahead_high_values=(0.0, 0.0, 0.0),
            cv_mean_rmse=3.2,
            cv_stdev_rmse=0.4,
            training_rows=120,
            test_rows=40,
            feature_columns=("bm_wind", "solar"),
            range_mode="kde",
        ),
    )
    monkeypatch.setattr(update_forecast.settings, "ml_write_mode", "ml")

    with pytest.raises(RuntimeError) as exc_info:
        update_forecast.run_update_forecast_job(uow=cast(Any, uow))

    assert "degenerate" in str(exc_info.value)
