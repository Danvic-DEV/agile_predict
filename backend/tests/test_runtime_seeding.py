from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast
import pytest

from src.core import runtime


@dataclass
class _Result:
    records_written: int
    forecast_name: str


class _FakeUow:
    def __init__(self) -> None:
        self.rollback_called = False

    def rollback(self) -> None:
        self.rollback_called = True


def test_seed_empty_database_prefers_update_mode(monkeypatch) -> None:
    uow = _FakeUow()

    monkeypatch.setattr(runtime.settings, "auto_bootstrap_mode", "update")

    def _fake_run_update_forecast_job(*, uow: Any) -> _Result:
        return _Result(records_written=12, forecast_name="from-update")

    def _fail_bootstrap(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("bootstrap should not run when update writes records")

    monkeypatch.setattr(runtime, "run_update_forecast_job", _fake_run_update_forecast_job)
    monkeypatch.setattr(runtime, "_write_bootstrap_seed", _fail_bootstrap)

    mode = runtime.seed_empty_database(uow=cast(Any, uow))

    assert mode == "update"
    assert uow.rollback_called is False


def test_seed_empty_database_raises_when_update_writes_zero_and_fallback_disabled(monkeypatch) -> None:
    uow = _FakeUow()

    monkeypatch.setattr(runtime.settings, "auto_bootstrap_mode", "update")
    monkeypatch.setattr(runtime.settings, "allow_startup_bootstrap_fallback", False)

    def _fake_run_update_forecast_job(*, uow: Any) -> _Result:
        return _Result(records_written=0, forecast_name="empty")

    monkeypatch.setattr(runtime, "run_update_forecast_job", _fake_run_update_forecast_job)

    with pytest.raises(RuntimeError):
        runtime.seed_empty_database(uow=cast(Any, uow))


def test_seed_empty_database_bootstrap_mode(monkeypatch) -> None:
    uow = _FakeUow()
    bootstrap_calls = {"count": 0}

    monkeypatch.setattr(runtime.settings, "auto_bootstrap_mode", "bootstrap")

    def _bootstrap(uow: Any) -> None:
        bootstrap_calls["count"] += 1

    monkeypatch.setattr(runtime, "_write_bootstrap_seed", _bootstrap)

    mode = runtime.seed_empty_database(uow=cast(Any, uow))

    assert mode == "bootstrap"
    assert bootstrap_calls["count"] == 1


def test_seed_empty_database_update_exception_raises_when_fallback_disabled(monkeypatch) -> None:
    uow = _FakeUow()

    monkeypatch.setattr(runtime.settings, "auto_bootstrap_mode", "update")
    monkeypatch.setattr(runtime.settings, "allow_startup_bootstrap_fallback", False)

    def _raise_update(*, uow: Any) -> _Result:
        raise RuntimeError("simulated update failure")

    monkeypatch.setattr(runtime, "run_update_forecast_job", _raise_update)

    with pytest.raises(RuntimeError):
        runtime.seed_empty_database(uow=cast(Any, uow))

    assert uow.rollback_called is True


def test_seed_empty_database_bootstrap_fallback_can_be_explicitly_enabled(monkeypatch) -> None:
    uow = _FakeUow()
    bootstrap_calls = {"count": 0}

    monkeypatch.setattr(runtime.settings, "auto_bootstrap_mode", "update")
    monkeypatch.setattr(runtime.settings, "allow_startup_bootstrap_fallback", True)

    def _raise_update(*, uow: Any) -> _Result:
        raise RuntimeError("simulated update failure")

    def _bootstrap(uow: Any) -> None:
        bootstrap_calls["count"] += 1

    monkeypatch.setattr(runtime, "run_update_forecast_job", _raise_update)
    monkeypatch.setattr(runtime, "_write_bootstrap_seed", _bootstrap)

    mode = runtime.seed_empty_database(uow=cast(Any, uow))

    assert mode == "bootstrap-fallback"
    assert bootstrap_calls["count"] == 1
