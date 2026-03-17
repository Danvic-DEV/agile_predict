from fastapi.testclient import TestClient

import src.main as main_module


def _client_without_runtime(monkeypatch) -> TestClient:
    monkeypatch.setattr(main_module, "initialize_runtime", lambda: None)
    return TestClient(main_module.create_app())


def test_healthz_contract(monkeypatch) -> None:
    client = _client_without_runtime(monkeypatch)

    response = client.get("/healthz")

    assert response.status_code == 200
    payload = response.json()
    assert set(payload.keys()) == {"status", "service"}
    assert payload["status"] == "ok"


def test_api_health_contract(monkeypatch) -> None:
    client = _client_without_runtime(monkeypatch)

    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_regions_contract(monkeypatch) -> None:
    client = _client_without_runtime(monkeypatch)

    response = client.get("/api/v1/forecasts/regions")

    assert response.status_code == 200
    regions = response.json()
    assert isinstance(regions, list)
    assert len(regions) == 15
    assert regions == sorted(regions)
    assert "G" in regions
    assert "X" in regions
