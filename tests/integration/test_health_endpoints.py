from __future__ import annotations

from fastapi.testclient import TestClient


class DummySessionWithExecute:
    def execute(self, *_args, **_kwargs):
        return None

    def close(self):
        return None


def test_app_service_health_deep_returns_payload(app_paths, load_module, monkeypatch):
    module = load_module("app_service_main_health", app_paths["app_service"])
    module.app.dependency_overrides[module.get_session] = lambda: DummySessionWithExecute()
    monkeypatch.setattr(
        module,
        "build_health_report",
        lambda session, settings: (
            {
                "status": "ok",
                "service": "internal-assistant-mvp",
                "environment": "local",
                "checks": {"database": {"ok": True}},
            },
            True,
        ),
    )
    client = TestClient(module.app)

    response = client.get("/api/health/deep")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_app_service_serves_demo_ui(app_paths, load_module):
    module = load_module("app_service_main_demo_ui", app_paths["app_service"])
    client = TestClient(module.app)

    root_response = client.get("/")
    demo_response = client.get("/demo")
    js_response = client.get("/static/app.js")
    css_response = client.get("/static/styles.css")

    assert root_response.status_code == 200
    assert "LogiAssist Demo Console" in root_response.text
    assert demo_response.status_code == 200
    assert "LogiAssist Demo Console" in demo_response.text
    assert js_response.status_code == 200
    assert "requestJson" in js_response.text
    assert css_response.status_code == 200
    assert ".shell" in css_response.text


def test_local_functions_health_endpoints_are_available(app_paths, load_module):
    incidents_module = load_module("custom_incidents_health", app_paths["custom_incidents"])
    indexer_module = load_module("indexer_health", app_paths["indexer"])
    incidents_client = TestClient(incidents_module.app)
    indexer_client = TestClient(indexer_module.app)

    incidents_response = incidents_client.get("/health")
    indexer_response = indexer_client.get("/health")

    assert incidents_response.status_code == 200
    assert incidents_response.json()["service"] == "custom-incidents-api-function"
    assert indexer_response.status_code == 200
    assert indexer_response.json()["service"] == "indexer-function"
