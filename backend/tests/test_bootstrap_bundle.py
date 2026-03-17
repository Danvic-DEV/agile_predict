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


class _ForecastWrites:
    def __init__(self) -> None:
        self.rows: dict[str, _ForecastRow] = {}
        self._next_id = 1

    def get_by_name(self, name: str):
        return self.rows.get(name)

    def create_forecast(self, name: str, created_at: datetime, mean=None, stdev=None):
        row = _ForecastRow(id=self._next_id, name=name, created_at=created_at)
        self.rows[name] = row
        self._next_id += 1
        return row


class _ForecastDataWrites:
    def __init__(self) -> None:
        self.deleted: list[int] = []
        self.last_insert_count = 0

    def delete_for_forecast(self, forecast_id: int) -> int:
        self.deleted.append(forecast_id)
        return 0

    def bulk_insert(self, rows):
        self.last_insert_count = len(rows)
        return len(rows)


class _AgileDataWrites:
    def __init__(self) -> None:
        self.deleted: list[int] = []
        self.last_insert_count = 0

    def delete_for_forecast(self, forecast_id: int) -> int:
        self.deleted.append(forecast_id)
        return 0

    def bulk_insert(self, rows):
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
