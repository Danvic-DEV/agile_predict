from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, cast

from src.api.v1.routes import diagnostics as diagnostics_routes


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def scalar_one(self):
        return self._value


class _FakeSession:
    def __init__(self, values):
        self._values = list(values)
        self.calls: list[str] = []

    def execute(self, statement):
        self.calls.append(str(statement))
        return _ScalarResult(self._values.pop(0))


class _FakeUow:
    def __init__(self, session: _FakeSession):
        self.session = session


def _healthy_audit_values_for(forecast):
    return [
        forecast,
        96,
        96,
        96,
        0,
        datetime.now(timezone.utc) - timedelta(minutes=15),
    ]


def test_truth_audit_prefers_update_state_forecast_name(monkeypatch) -> None:
    forecast = SimpleNamespace(id=42, created_at=datetime.now(timezone.utc) - timedelta(minutes=5))
    session = _FakeSession(_healthy_audit_values_for(forecast))

    monkeypatch.setattr(
        diagnostics_routes,
        "read_last_update_job_state",
        lambda: {"forecast_name": "bundle::forecast::region-g"},
    )

    result = diagnostics_routes.pipeline_truth_audit(uow=cast(Any, _FakeUow(session)))

    assert result.trust_level == "high"
    assert result.latest_forecast_id == 42
    assert "name = :name_1" in session.calls[0]


def test_truth_audit_falls_back_to_non_history_forecast(monkeypatch) -> None:
    forecast = SimpleNamespace(id=314, created_at=datetime.now(timezone.utc) - timedelta(minutes=4))
    session = _FakeSession([None, *_healthy_audit_values_for(forecast)])

    monkeypatch.setattr(
        diagnostics_routes,
        "read_last_update_job_state",
        lambda: {"forecast_name": "bundle::forecast::region-g"},
    )

    result = diagnostics_routes.pipeline_truth_audit(uow=cast(Any, _FakeUow(session)))

    assert result.trust_level == "high"
    assert result.latest_forecast_id == 314
    assert "name = :name_1" in session.calls[0]
    assert "NOT LIKE" in session.calls[1]
