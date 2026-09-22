"""Проверка транзакционного подключения обработчиков к реальному API приёма."""

from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from hackneft_platform import api
from hackneft_platform.advisor.rules import Policy
from hackneft_platform.advisor.schema import AdvisorInput
from hackneft_platform.advisor.service import AdvisorService
from hackneft_platform.db import Base
from hackneft_platform.db import get_db as database_dependency
from hackneft_platform.events import SensorDataCreated, dispatcher
from hackneft_platform.models import SensorData


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient, None, None]:
    engine = create_engine(f"sqlite:///{tmp_path / 'api.db'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine)
    service = AdvisorService(sessions, Policy())

    def get_db() -> Generator[Session, None, None]:
        with sessions() as db:
            yield db

    async def no_sse(_: SensorDataCreated) -> None:
        pass

    monkeypatch.setattr(dispatcher, "publish", no_sse)
    api.app.dependency_overrides[database_dependency] = get_db
    api.app.state.advisor = service
    # Без lifespan: тест не запускает реальный HTTP-клиент ИИ.
    yield TestClient(api.app)
    api.app.dependency_overrides.clear()
    engine.dispose()


def test_bulk_duplicate_and_completion_marker(client: TestClient) -> None:
    payload = {
        "items": [
            {
                "timestamp": "2026-07-12T10:00:00",
                "sensor_code": code,
                "value": value,
                "source": "test",
            }
            for code, value in {
                "pack_24-2000:mg.sulfur": 11,
                "ht_t6": 360,
                "ht_p13": 3.8,
                "ht_f9": 200,
                "ht_f25": 13000,
                "ht_f2": 90000,
            }.items()
        ],
        "observed_at": "2026-07-12T10:10:00",
        "complete": False,
    }
    first = client.post("/api/sensor-data/bulk", json=payload)
    assert first.status_code == 201
    assert first.json() == {"accepted": 6, "duplicates": 0}
    assert client.post("/api/sensor-data/bulk", json=payload).json()["duplicates"] == 6
    service = api.app.state.advisor
    service.process()
    assert service.reserve() is None
    assert (
        client.post(
            "/api/sensor-data/bulk",
            json={
                "items": [],
                "observed_at": "2026-07-12T10:10:00",
                "complete": True,
            },
        ).status_code
        == 201
    )
    service.process()
    assert service.reserve()
    assert len(client.get("/api/advisor/events").json()["items"]) == 1
    status = client.get("/api/advisor/status").json()
    assert status["interval_minutes"] == 30
    assert status["active_run_id"] is not None


def test_single_duplicate_does_not_create_second_input(client: TestClient) -> None:
    payload = {
        "timestamp": "2026-07-12T10:00:00",
        "sensor_code": "ht_t6",
        "value": 360,
        "source": "scada",
    }
    assert client.post("/api/sensor-data", json=payload).status_code == 201
    assert client.post("/api/sensor-data", json=payload).status_code == 200
    with api.app.state.advisor.sessions() as db:
        assert db.scalar(select(func.count()).select_from(SensorData)) == 1
        assert db.scalar(select(func.count()).select_from(AdvisorInput)) == 1


def test_future_measurement_is_rejected_before_write(client: TestClient) -> None:
    response = client.post(
        "/api/sensor-data/bulk",
        json={
            "items": [
                {
                    "timestamp": "2026-07-12T11:00:00",
                    "sensor_code": "ht_t6",
                    "value": 360,
                    "source": "scada",
                }
            ],
            "observed_at": "2026-07-12T10:00:00",
        },
    )
    assert response.status_code == 422
    with api.app.state.advisor.sessions() as db:
        assert db.scalar(select(func.count()).select_from(SensorData)) == 0
