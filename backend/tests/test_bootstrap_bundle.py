from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, cast

from src.domain.bootstrap_bundle import BootstrapBundleConfig, write_bootstrap_bundle


@dataclass
class _ForecastRow:
    id: int
    name: str
    created_at: datetime
    mean: float | None = None
    stdev: float | None = None


class _ForecastWrites:
    def __init__(self) -> None:
        self.rows: dict[str, _ForecastRow] = {}
        self._next_id = 1

    def get_by_name(self, name: str):
        return self.rows.get(name)

    def create_forecast(self, name: str, created_at: datetime, mean=None, stdev=None):
        row = _ForecastRow(id=self._next_id, name=name, created_at=created_at, mean=mean, stdev=stdev)
        self.rows[name] = row
        self._next_id += 1
        return row


class _ForecastDataWrites:
    def __init__(self) -> None:
        self.deleted: list[int] = []
        self.last_insert_count = 0
        self.rows = []

    def delete_for_forecast(self, forecast_id: int) -> int:
        self.deleted.append(forecast_id)
        return 0

    def bulk_insert(self, rows):
        self.rows = list(rows)
        self.last_insert_count = len(rows)
        return len(rows)


class _AgileDataWrites:
    def __init__(self) -> None:
        self.deleted: list[int] = []
        self.last_insert_count = 0
        self.rows = []

    def delete_for_forecast(self, forecast_id: int) -> int:
        self.deleted.append(forecast_id)
        return 0

    def bulk_insert(self, rows):
        self.rows = list(rows)
        self.last_insert_count = len(rows)
        return len(rows)


class _FakeUow:
    def __init__(self) -> None:
        self.forecast_writes = _ForecastWrites()
        self.forecast_data_writes = _ForecastDataWrites()
        self.agile_data_writes = _AgileDataWrites()


def test_bundle_write_is_idempotent_with_key() -> None:
    uow = _FakeUow()
    cfg = BootstrapBundleConfig(
        points=4,
        idempotency_key="repeatable",
        replace_existing=True,
        regions=("X", "G"),
        write_agile_data=True,
    )

    first = write_bootstrap_bundle(uow=cast(Any, uow), config=cfg)
    second = write_bootstrap_bundle(uow=cast(Any, uow), config=cfg)

    assert first.forecast_name == second.forecast_name
    assert first.forecast_id == second.forecast_id
    assert first.idempotent_hit is False
    assert second.idempotent_hit is True
    assert second.forecast_data_points_written == 4
    assert second.agile_data_points_written == 8


def test_bundle_write_without_agile_data() -> None:
    uow = _FakeUow()
    cfg = BootstrapBundleConfig(
        points=6,
        idempotency_key="no-agile",
        replace_existing=True,
        regions=("X",),
        write_agile_data=False,
    )

    result = write_bootstrap_bundle(uow=cast(Any, uow), config=cfg)

    assert result.forecast_data_points_written == 6
    assert result.agile_data_points_written == 0


def test_bundle_uses_day_ahead_bounds_and_persists_metrics() -> None:
    uow = _FakeUow()
    cfg = BootstrapBundleConfig(
        points=2,
        idempotency_key="with-bounds",
        replace_existing=True,
        regions=("G",),
        day_ahead_values=(100.0, 110.0),
        day_ahead_low_values=(95.0, 105.0),
        day_ahead_high_values=(108.0, 120.0),
        forecast_mean=4.2,
        forecast_stdev=0.7,
        write_agile_data=True,
    )

    result = write_bootstrap_bundle(uow=cast(Any, uow), config=cfg)

    assert result.forecast_data_points_written == 2
    assert result.agile_data_points_written == 2

    forecast = uow.forecast_writes.rows[result.forecast_name]
    assert forecast.mean == 4.2
    assert forecast.stdev == 0.7

    first = uow.agile_data_writes.rows[0]
    assert first.agile_low <= first.agile_pred <= first.agile_high
