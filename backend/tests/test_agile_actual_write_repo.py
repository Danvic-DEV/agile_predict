from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from src.repositories.agile_actual_write_repo import AgileActualWriteRepository, _UPSERT_BATCH_SIZE


class _FakeSession:
    def __init__(self) -> None:
        self.execute_calls: list[object] = []
        self.flush_calls = 0

    def execute(self, stmt: object) -> SimpleNamespace:
        self.execute_calls.append(stmt)
        return SimpleNamespace(rowcount=1)

    def flush(self) -> None:
        self.flush_calls += 1


def test_upsert_many_returns_zero_for_empty_rows() -> None:
    session = _FakeSession()
    repo = AgileActualWriteRepository(session)

    assert repo.upsert_many([]) == 0
    assert session.execute_calls == []
    assert session.flush_calls == 0


def test_upsert_many_batches_large_payloads() -> None:
    session = _FakeSession()
    repo = AgileActualWriteRepository(session)
    row_count = (_UPSERT_BATCH_SIZE * 2) + 123
    rows = [
        {
            "date_time": datetime(2026, 3, 1, tzinfo=timezone.utc),
            "region": "B",
            "agile_actual": float(index),
        }
        for index in range(row_count)
    ]

    result = repo.upsert_many(rows)

    assert result == 3
    assert len(session.execute_calls) == 3
    assert session.flush_calls == 1