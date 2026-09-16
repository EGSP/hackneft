from fastapi.testclient import TestClient

from hackneft_platform.api import app
from hackneft_platform.db import init_db


def test_health_endpoint() -> None:
    response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_create_measurement_endpoint() -> None:
    init_db()

    with TestClient(app) as client:
        response = client.post(
            "/measurements",
            json={"source": "telemetry", "tag": "T5", "value": 372.5},
        )

    assert response.status_code == 201
    assert response.json()["source"] == "telemetry"
    assert response.json()["tag"] == "T5"
    assert response.json()["value"] == 372.5
