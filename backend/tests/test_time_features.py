import pandas as pd

from src.ml.features import add_time_features


def test_add_time_features_adds_expected_columns() -> None:
    index = pd.date_range("2026-01-03 15:00", periods=4, freq="30min", tz="UTC")
    df = pd.DataFrame(index=index, data={"value": [1, 2, 3, 4]})

    out = add_time_features(df)

    for col in ["time", "day_of_week", "weekend", "peak"]:
        assert col in out.columns

    assert out["weekend"].isin([0, 1]).all()
    assert out["peak"].isin([0, 1]).all()
