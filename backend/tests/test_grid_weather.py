import pandas as pd

from src.ml.ingest import grid_weather


def test_fetch_neso_demand_merges_historic_recent_and_forecast(monkeypatch) -> None:
    captured_params: dict[str, str] = {}

    def fake_get_json(url: str, params: dict | None = None, timeout: int = 30):
        assert url == "https://data.elexon.co.uk/bmrs/api/v1/datasets/INDO"
        assert params is not None
        captured_params.update(params)
        return {
            "data": [
                {
                    "startTime": "2026-03-20T00:00:00Z",
                    "demand": 20000,
                }
            ]
        }

    def fake_neso_sql(resource_id: str, where_clause: str, limit: int = 40000) -> pd.DataFrame:
        if resource_id == "f6d02c0f-957b-48cb-82ee-09003f2ba759":
            return pd.DataFrame(
                {
                    "SETTLEMENT_DATE": ["2024-03-01"],
                    "SETTLEMENT_PERIOD": [1],
                    "ND": [15000],
                }
            )
        return pd.DataFrame(columns=["SETTLEMENT_DATE", "SETTLEMENT_PERIOD", "ND"])

    monkeypatch.setattr(grid_weather, "_get_json", fake_get_json)
    monkeypatch.setattr(grid_weather, "_retry", lambda fn, retries=3, backoff=2.0: fn())
    monkeypatch.setattr(grid_weather, "_neso_sql", fake_neso_sql)
    monkeypatch.setattr(
        grid_weather,
        "_fetch_elexon_ndf_forecast",
        lambda: pd.Series(
            [21000.0],
            index=pd.DatetimeIndex(["2026-03-25T00:00:00Z"]),
        ),
    )

    series = grid_weather._fetch_neso_demand("2024-03-01")

    assert "publishDateTimeFrom" not in captured_params
    assert captured_params["startTimeFrom"].endswith("Z")
    assert float(series.loc[pd.Timestamp("2024-03-01T00:00:00Z")]) == 15000.0
    assert float(series.loc[pd.Timestamp("2026-03-20T00:00:00Z")]) == 20000.0
    assert float(series.loc[pd.Timestamp("2026-03-25T00:00:00Z")]) == 21000.0


def test_fetch_neso_historic_demand_queries_only_relevant_years(monkeypatch) -> None:
    requested_resources: list[str] = []

    def fake_neso_sql(resource_id: str, where_clause: str, limit: int = 40000) -> pd.DataFrame:
        requested_resources.append(resource_id)
        return pd.DataFrame(columns=["SETTLEMENT_DATE", "SETTLEMENT_PERIOD", "ND"])

    monkeypatch.setattr(grid_weather, "_neso_sql", fake_neso_sql)

    series = grid_weather._fetch_neso_historic_demand(pd.Timestamp("2025-01-01", tz="UTC"))

    assert series.empty
    assert requested_resources == [
        "b2bde559-3455-4021-b179-dfe60c0337b0",
        "8a4a771c-3929-4e56-93ad-cdf13219dea5",
    ]