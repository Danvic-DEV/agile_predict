from __future__ import annotations

from datetime import datetime, timezone

from src.domain import forecast_pipeline


def test_run_forecast_pipeline_uses_nordpool_series_when_available(monkeypatch) -> None:
    def _fake_fetch_day_ahead_prices(*, now: datetime | None = None, timeout: int = 20) -> dict[datetime, float]:
        del now, timeout
        return {
            datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc): 80.0,
            datetime(2026, 1, 1, 0, 30, tzinfo=timezone.utc): 81.0,
            datetime(2026, 1, 1, 1, 0, tzinfo=timezone.utc): 82.0,
        }

    monkeypatch.setattr(forecast_pipeline, "fetch_day_ahead_prices", _fake_fetch_day_ahead_prices)

    result = forecast_pipeline.run_forecast_pipeline(now=datetime(2026, 1, 1, tzinfo=timezone.utc), fallback_points=6)

    assert result.source == "nordpool"
    assert result.ingest_error is None
    assert result.retries_used == 0
    assert result.raw_points == 3
    assert len(result.day_ahead_values) == 6
    assert result.day_ahead_values[:3] == (80.0, 81.0, 82.0)
    assert result.agile_preview_mean > 0


def test_run_forecast_pipeline_falls_back_when_ingest_fails(monkeypatch) -> None:
    def _raise_fetch_day_ahead_prices(*, now: datetime | None = None, timeout: int = 20) -> dict[datetime, float]:
        del now, timeout
        raise RuntimeError("upstream unavailable")

    monkeypatch.setattr(forecast_pipeline, "fetch_day_ahead_prices", _raise_fetch_day_ahead_prices)
    monkeypatch.setattr(forecast_pipeline.time, "sleep", lambda _seconds: None)

    result = forecast_pipeline.run_forecast_pipeline(now=datetime(2026, 1, 1, tzinfo=timezone.utc), fallback_points=8)

    assert result.source == "fallback"
    assert result.ingest_error is not None
    assert result.retries_used == 2
    assert result.raw_points == 8
    assert len(result.day_ahead_values) == 8
    assert result.day_ahead_values[0] == 80.0
