"""EQP-1 smoke tests: the FastAPI backend boots and documents itself."""

from fastapi.testclient import TestClient

from server.main import create_app


def test_health_returns_ok() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_swagger_ui_is_served() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/docs")

    assert response.status_code == 200
    assert "swagger-ui" in response.text.lower()


def test_openapi_schema_names_the_service() -> None:
    with TestClient(create_app()) as client:
        schema = client.get("/openapi.json").json()

    assert schema["info"]["title"] == "EquipDesk API"
    assert "/health" in schema["paths"]
