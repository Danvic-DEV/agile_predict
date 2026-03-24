from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, cast

import pandas as pd
import pytest

from src.domain.forecast_pipeline import ForecastPipelineOutput
from src.jobs.pipelines import update_forecast
from src.ml.ingest import grid_weather


class _FakeUow:
    pass


def test_fetch_open_meteo_clamps_archive_end_date(monkeypatch) -> None:
    today = pd.Timestamp.now(tz="UTC").normalize()
    future_end = (today + pd.Timedelta(days=3)).strftime("%Y-%m-%d")
    captured_calls: list[tuple[str, dict[str, str]]] = []

    def fake_get_json(url: str, params: dict | None = None, timeout: int = 30):
        assert params is not None
        captured_calls.append((url, params))
        if "archive-api.open-meteo.com" in url:
            return {
                "hourly": {
                    "time": [today.strftime("%Y-%m-%dT00:00")],
                    "temperature_2m": [10.0],
                    "wind_speed_10m": [5.0],
                    "direct_radiation": [100.0],
                }
            }
        return {
            "hourly": {
                "time": [today.strftime("%Y-%m-%dT00:00")],
                "temperature_2m": [11.0],
                "wind_speed_10m": [6.0],
                "direct_radiation": [120.0],
            }
        }

    monkeypatch.setattr(grid_weather, "_get_json", fake_get_json)
    monkeypatch.setattr(grid_weather, "_retry", lambda fn, retries=3, backoff=2.0: fn())

    result = grid_weather._fetch_open_meteo(today.strftime("%Y-%m-%d"), future_end)

    assert not result.empty
    archive_call = next(params for url, params in captured_calls if "archive-api.open-meteo.com" in url)
    assert archive_call["end_date"] == today.strftime("%Y-%m-%d")


def test_run_update_forecast_job_fails_closed_on_short_live_feature_horizon(monkeypatch) -> None:
    anchor = pd.Timestamp(datetime.now(timezone.utc)).floor("30min")
    short_features = pd.DataFrame(
        {
            "bm_wind": [1.0, 2.0],
            "solar": [3.0, 4.0],
            "emb_wind": [5.0, 6.0],
            "demand": [7.0, 8.0],
            "temp_2m": [9.0, 10.0],
            "wind_10m": [11.0, 12.0],
            "rad": [13.0, 14.0],
        },
        index=pd.DatetimeIndex([anchor, anchor + pd.Timedelta("30min")]),
    )

    monkeypatch.setattr(
        update_forecast,
        "run_forecast_pipeline",
        lambda **kwargs: ForecastPipelineOutput(
            day_ahead_values=(70.0, 71.0, 72.0),
            source="nordpool",
            agile_preview_mean=18.0,
        ),
    )
    monkeypatch.setattr(update_forecast, "check_ml_training_readiness", lambda **kwargs: (False, None))
    monkeypatch.setattr(update_forecast, "fetch_live_forecast_features", lambda **kwargs: short_features)
    monkeypatch.setattr(update_forecast.settings, "allow_partial_forecast_horizon", False)
    monkeypatch.setattr(update_forecast, "write_bootstrap_bundle", lambda **kwargs: pytest.fail("should not write partial forecast"))

    with pytest.raises(RuntimeError, match="insufficient forward feature rows for full horizon"):
        update_forecast.run_update_forecast_job(uow=cast(Any, _FakeUow()))


def test_run_update_forecast_job_allows_partial_horizon_when_enabled(monkeypatch) -> None:
    anchor = pd.Timestamp(datetime.now(timezone.utc)).floor("30min")
    short_features = pd.DataFrame(
        {
            "bm_wind": [1.0, 2.0],
            "solar": [3.0, 4.0],
            "emb_wind": [5.0, 6.0],
            "demand": [7.0, 8.0],
            "temp_2m": [9.0, 10.0],
            "wind_10m": [11.0, 12.0],
            "rad": [13.0, 14.0],
        },
        index=pd.DatetimeIndex([anchor, anchor + pd.Timedelta("30min")]),
    )

    monkeypatch.setattr(
        update_forecast,
        "run_forecast_pipeline",
        lambda **kwargs: ForecastPipelineOutput(
            day_ahead_values=(70.0, 71.0, 72.0),
            source="nordpool",
            agile_preview_mean=18.0,
        ),
    )
    monkeypatch.setattr(update_forecast, "check_ml_training_readiness", lambda **kwargs: (False, None))
    monkeypatch.setattr(update_forecast, "fetch_live_forecast_features", lambda **kwargs: short_features)
    monkeypatch.setattr(update_forecast.settings, "allow_partial_forecast_horizon", True)
    monkeypatch.setattr(update_forecast, "fetch_grid_weather_features", lambda **kwargs: pd.DataFrame())
    monkeypatch.setattr(update_forecast, "fetch_day_ahead_prices", lambda **kwargs: {})
    monkeypatch.setattr(update_forecast, "fetch_agile_prices_all_regions", lambda **kwargs: {})
    monkeypatch.setattr(update_forecast, "fetch_system_context_features", lambda **kwargs: pd.DataFrame())
    monkeypatch.setattr(update_forecast, "prune_old_forecasts", lambda **kwargs: 0)
    monkeypatch.setattr(update_forecast, "prune_update_job_forecasts", lambda **kwargs: 0)
    monkeypatch.setattr(update_forecast, "write_last_update_job_state", lambda **kwargs: None)
    monkeypatch.setattr(update_forecast, "send_update_success_notification", lambda **kwargs: None)
    monkeypatch.setattr(update_forecast, "send_daily_digest_notification", lambda **kwargs: None)
    monkeypatch.setattr(update_forecast, "send_parity_alert_notification", lambda **kwargs: None)
    monkeypatch.setattr(update_forecast, "send_pipeline_staleness_alert_notification", lambda **kwargs: None)
    monkeypatch.setattr(update_forecast, "clear_pipeline_staleness_alert_state", lambda: None)
    monkeypatch.setattr(update_forecast, "send_gpu_alert_notification", lambda **kwargs: None)

    captured: dict[str, Any] = {}

    class _FakeResult:
        def __init__(self) -> None:
            self.forecast_name = "bundle::update-job-seed"
            self.forecast_data_points_written = 2
            self.agile_data_points_written = 4

    def _fake_write_bootstrap_bundle(*, uow: Any, config: Any) -> _FakeResult:
        captured["config"] = config
        return _FakeResult()

    monkeypatch.setattr(update_forecast, "write_bootstrap_bundle", _fake_write_bootstrap_bundle)

    result = update_forecast.run_update_forecast_job(uow=cast(Any, _FakeUow()))

    assert captured["config"].points == 2
    assert captured["config"].day_ahead_values == (70.0, 71.0)
    assert result.day_ahead_points == 2
    assert result.source == "nordpool:partial-horizon"