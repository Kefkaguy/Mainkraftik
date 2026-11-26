from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_paperpilot import paperdb  # noqa: F401

from chart_intelligence import store
from chart_intelligence.api import router
from intelligence.security import require_session


def test_research_api_auth_and_no_execution_routes(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "STORE", tmp_path)
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    assert client.get("/v1/chart-intelligence/status").status_code == 401
    assert client.get("/v1/chart-intelligence/phase7").status_code == 401
    app.dependency_overrides[require_session] = lambda: {"fixture": True}
    result = client.get("/v1/chart-intelligence/status")
    assert result.status_code == 200
    assert result.json()["deployment"]["execution_authority"] is False
    assert client.post("/v1/chart-intelligence/status").status_code == 405
    assert client.post("/v1/chart-intelligence/promote").status_code == 404
    phase7 = client.get("/v1/chart-intelligence/phase7")
    assert phase7.status_code == 200
    assert phase7.json()["execution_authority"] is False
    assert all("trades" not in row["base_scenario"] for row in phase7.json()["audit"].values())


def test_trace_api_requires_private_token(paperdb, monkeypatch):  # noqa: F811
    from paperpilot import service

    monkeypatch.setattr(service.settings, "paper_internal_token", "fixture-token")
    client = TestClient(service.app)
    assert client.get("/decision-traces").status_code == 403
    result = client.get("/decision-traces", headers={"x-pilot-token": "fixture-token"})
    assert result.status_code == 200 and result.json()["signals"] == []
