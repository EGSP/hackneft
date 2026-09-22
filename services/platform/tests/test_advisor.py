"""Сценарии событий и общего допуска, без вызовов настоящей языковой модели."""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from hackneft_platform.advisor.rules import Policy
from hackneft_platform.advisor.runtime import AdvisorWorker
from hackneft_platform.advisor.schema import AdvisorEvent, AdvisorInput, AdvisorRun
from hackneft_platform.advisor.service import AdvisorService, record_input
from hackneft_platform.catalog import SULFUR_LIMS_CODE, SULFUR_PAK_CODE
from hackneft_platform.db import Base
from hackneft_platform.events import SensorDataCreated

START = datetime(2026, 7, 12, 10)
NORMAL = {
    SULFUR_PAK_CODE: 7.0,
    "ht_t6": 360.0,
    "ht_p13": 3.8,
    "ht_f9": 200.0,
    "ht_f25": 13000.0,
    "ht_f2": 90000.0,
    "ht_q20": 8000.0,
}


def active(service: AdvisorService) -> dict[str, Any]:
    run = service.active_run()
    assert run is not None
    return run


@pytest.fixture
def service(tmp_path: Path) -> AdvisorService:
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    return AdvisorService(sessionmaker(engine), Policy())


def push(
    service: AdvisorService,
    minute: int,
    values: dict[str, float] | None = None,
    *,
    complete: bool = True,
    sample_at: datetime | None = None,
) -> None:
    at = START + timedelta(minutes=minute)
    readings = NORMAL if values is None else values
    with service.sessions() as db:
        record_input(
            db,
            [
                SensorDataCreated(
                    event_id=f"{minute}-{code}",
                    data_id=minute * 100 + i,
                    timestamp=sample_at or at,
                    sensor_code=code,
                    value=value,
                    source="test",
                )
                for i, (code, value) in enumerate(readings.items())
            ],
            observed_at=at,
            complete=complete,
        )
        db.commit()
    service.process()


def test_quiet_period_does_not_launch(service: AdvisorService) -> None:
    for minute in range(0, 181, 10):
        push(service, minute)
        assert service.reserve() is None
    assert service.events() == []


def test_warning_requires_full_thirty_minutes_and_does_not_repeat(service: AdvisorService) -> None:
    for minute in (0, 10, 20):
        push(service, minute, NORMAL | {SULFUR_PAK_CODE: 8.5})
        assert service.reserve() is None
    push(service, 30, NORMAL | {SULFUR_PAK_CODE: 8.5})
    run_id = service.reserve()
    assert run_id is not None
    assert service.claim(run_id)
    service.update_run(run_id, "completed")
    for minute in (40, 50, 60):
        push(service, minute, NORMAL | {SULFUR_PAK_CODE: 8.5})
        assert service.reserve() is None
    assert len([e for e in service.events() if e["kind"] == "sulfur_risk"]) == 1


def test_gap_does_not_count_as_sustained_warning(service: AdvisorService) -> None:
    for minute in (0, 20, 30):
        push(service, minute, NORMAL | {SULFUR_PAK_CODE: 8.5})
    assert service.reserve() is None


def test_six_checks_coalesce_and_critical_does_not_bypass_interval(service: AdvisorService) -> None:
    push(service, 0, NORMAL | {SULFUR_PAK_CODE: 11})
    first = service.reserve()
    assert first
    assert service.claim(first)
    service.update_run(first, "completed")
    changed = NORMAL | {SULFUR_PAK_CODE: 11, "ht_q20": 10000, "ht_t6": 368, "ht_f25": 9000}
    push(service, 10, changed)
    push(service, 20, changed | {SULFUR_LIMS_CODE: 11.6})
    assert service.reserve() is None
    push(service, 30, changed)
    second = service.reserve()
    assert second and second != first
    assert {r["kind"] for r in active(service)["reasons"]} == {
        "sulfur_risk",
        "feed_changed",
        "regime_changed",
        "gas_supply",
        "lab_result",
    }
    push(service, 60, changed | {SULFUR_LIMS_CODE: 12})
    assert service.reserve() is None  # Интервал прошёл, но прежний агент ещё активен.


def test_exact_limit_and_invalid_307_are_not_exceedances(service: AdvisorService) -> None:
    push(service, 0, NORMAL | {SULFUR_PAK_CODE: 10})
    assert service.reserve() is None
    push(service, 10, NORMAL | {SULFUR_PAK_CODE: 307})
    assert service.reserve() is None
    assert service.status()["blocked_by_data"] == [SULFUR_PAK_CODE]
    assert all(e["kind"] != "sulfur_risk" for e in service.events())


def test_worsening_same_incident_waits_for_shared_interval(service: AdvisorService) -> None:
    push(service, 0, NORMAL | {SULFUR_PAK_CODE: 11})
    run_id = service.reserve()
    assert run_id
    assert service.claim(run_id)
    service.update_run(run_id, "completed")
    push(service, 10, NORMAL | {SULFUR_PAK_CODE: 12.5})
    assert service.status()["pending_reasons"][0]["kind"] == "sulfur_risk"
    assert service.reserve() is None
    push(service, 30, NORMAL | {SULFUR_PAK_CODE: 12.5})
    assert service.reserve()


def test_incomplete_window_waits_and_restart_keeps_pending_inputs(service: AdvisorService) -> None:
    push(service, 0, NORMAL | {SULFUR_PAK_CODE: 11}, complete=False)
    assert service.reserve() is None
    resumed = AdvisorService(service.sessions, Policy())
    push(resumed, 0, {})
    assert resumed.reserve()
    assert service.reserve() is None


def test_duplicate_and_late_measurement_do_not_reopen_incident(service: AdvisorService) -> None:
    push(service, 20, NORMAL)
    push(service, 20, NORMAL | {SULFUR_PAK_CODE: 11})
    push(service, 10, NORMAL | {SULFUR_PAK_CODE: 11})
    assert service.reserve() is None
    assert service.events() == []


def test_lims_sample_time_is_preserved_without_second_delay(service: AdvisorService) -> None:
    push(service, 0)
    sampled = START - timedelta(hours=4)
    push(service, 0, {SULFUR_LIMS_CODE: 11.6}, sample_at=sampled)
    run_id = service.reserve()
    assert run_id
    run = active(service)
    assert run["snapshot"]["readings"][SULFUR_LIMS_CODE]["at"] == sampled.isoformat()
    assert run["requested_at"] == START
    assert service.status()["active_reasons"] == []  # Это историческая проба, не текущий ПАК.


def test_live_timer_detects_silence_but_replay_pause_does_not(service: AdvisorService) -> None:
    push(service, 0)
    service.process(START + timedelta(hours=1))
    assert service.status()["blocked_by_data"] == []
    live = AdvisorService(service.sessions, Policy(clock="live"))
    live.process(START + timedelta(hours=1))
    assert len(live.status()["blocked_by_data"]) == 6
    assert live.reserve() is None


def test_recovery_unblocks_existing_pending_request(service: AdvisorService) -> None:
    push(service, 0, NORMAL | {SULFUR_PAK_CODE: 11, "ht_f25": 307})
    assert service.reserve() is None
    push(service, 10, NORMAL | {SULFUR_PAK_CODE: 11})
    assert service.reserve()
    assert service.status()["blocked_by_data"] == []


def test_warning_clears_before_first_launch(service: AdvisorService) -> None:
    push(service, 0, NORMAL | {SULFUR_PAK_CODE: 11})
    push(service, 10)
    assert service.reserve() is None


def test_atomic_reservation_allows_one_worker(service: AdvisorService) -> None:
    push(service, 0, NORMAL | {SULFUR_PAK_CODE: 11})
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: service.reserve(), range(4)))
    assert sum(r is not None for r in results) == 1
    with service.sessions() as db:
        assert db.scalar(select(func.count()).select_from(AdvisorRun)) == 1


def test_queued_launch_refreshes_snapshot_and_waits_for_window(service: AdvisorService) -> None:
    push(service, 0, NORMAL | {SULFUR_PAK_CODE: 11})
    run_id = service.reserve()
    assert run_id
    push(service, 10, NORMAL | {SULFUR_PAK_CODE: 12.5}, complete=False)
    assert service.claim(run_id) is None
    push(service, 10, {})
    claimed = service.claim(run_id)
    assert claimed
    assert claimed["snapshot"]["readings"][SULFUR_PAK_CODE]["value"] == 12.5
    assert service.status()["next_allowed_at"] == START + timedelta(minutes=40)


def test_known_failure_retries_only_after_interval(service: AdvisorService) -> None:
    push(service, 0, NORMAL | {SULFUR_PAK_CODE: 11})
    run_id = service.reserve()
    assert run_id and service.claim(run_id)
    service.update_run(run_id, "failed", error="ИИ-сервис недоступен")
    assert service.reserve() is None
    push(service, 30, NORMAL | {SULFUR_PAK_CODE: 11})
    assert service.reserve()


def test_queued_warning_is_cancelled_when_cause_disappears(service: AdvisorService) -> None:
    push(service, 0, NORMAL | {SULFUR_PAK_CODE: 11})
    run_id = service.reserve()
    assert run_id
    push(service, 10)
    assert service.claim(run_id) is None
    assert service.active_run() is None
    assert service.status()["last_run"]["status"] == "cancelled"


def test_input_rollback_leaves_no_event(service: AdvisorService) -> None:
    with service.sessions() as db:
        record_input(db, [SensorDataCreated("x", 1, START, SULFUR_PAK_CODE, 11, "pak")])
        db.rollback()
    service.process()
    with service.sessions() as db:
        assert db.scalar(select(func.count()).select_from(AdvisorInput)) == 0
        assert db.scalar(select(func.count()).select_from(AdvisorEvent)) == 0


def test_http_launch_once_and_result_is_saved(service: AdvisorService) -> None:
    push(service, 0, NORMAL | {SULFUR_PAK_CODE: 11})
    posts = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            posts.append(request)
            return httpx.Response(200, json={"id": "session-1", "status": "running"})
        return httpx.Response(
            200, json={"id": "session-1", "status": "completed", "result": "Совет"}
        )

    async def scenario() -> None:
        async with httpx.AsyncClient(
            base_url="http://ai", transport=httpx.MockTransport(handle)
        ) as c:
            worker = AdvisorWorker(service, c)
            await worker.step()
            await worker.step()
            await worker.step()

    asyncio.run(scenario())
    assert len(posts) == 1
    assert json.loads(posts[0].content)["agent"] == "advisor"
    assert service.status()["last_run"]["result"] == "Совет"
    assert service.active_run() is None


def test_lost_http_response_is_reconciled_without_second_post(service: AdvisorService) -> None:
    push(service, 0, NORMAL | {SULFUR_PAK_CODE: 11})
    posts = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            posts.append(request)
            raise httpx.ReadTimeout("response lost")
        run = active(service)
        return httpx.Response(
            200,
            json={
                "sessions": [
                    {"id": "s1", "title": f"24-2000 / {run['id']}", "status": "running"},
                ]
            },
        )

    async def scenario() -> None:
        async with httpx.AsyncClient(
            base_url="http://ai", transport=httpx.MockTransport(handle)
        ) as c:
            worker = AdvisorWorker(service, c)
            await worker.step()
            assert active(service)["status"] == "unknown"
            push(service, 40, NORMAL | {SULFUR_LIMS_CODE: 12})
            await worker.step()

    asyncio.run(scenario())
    assert len(posts) == 1
    assert active(service)["session_id"] == "s1"
