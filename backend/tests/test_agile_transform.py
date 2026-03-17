import pandas as pd

from src.ml.transforms import agile_to_day_ahead, day_ahead_to_agile


def test_day_ahead_and_back_round_trip_non_peak() -> None:
    index = pd.date_range("2026-01-01 10:00", periods=2, freq="30min", tz="Europe/London")
    day_ahead = pd.Series(index=index, data=[80.0, 90.0])

    agile = day_ahead_to_agile(day_ahead, region="G")
    restored = agile_to_day_ahead(agile, region="G")

    assert restored.round(6).equals(day_ahead.round(6))


def test_day_ahead_adds_peak_offset_for_peak_hours() -> None:
    index = pd.date_range("2026-01-01 16:00", periods=2, freq="30min", tz="Europe/London")
    day_ahead = pd.Series(index=index, data=[100.0, 100.0])

    agile = day_ahead_to_agile(day_ahead, region="G")
    # region G uses factor 0.21 and peak offset 12
    assert float(agile.iloc[0]) == 33.0
    assert float(agile.iloc[1]) == 33.0
