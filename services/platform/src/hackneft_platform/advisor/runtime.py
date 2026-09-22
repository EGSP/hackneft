"""Один работник: сначала все проверки, затем максимум одна сессия советника."""

import asyncio
import logging
import os
import time
from typing import Any

import httpx

from hackneft_platform.config import aggregator_url, ai_service_url
from hackneft_platform.db import SessionLocal

from .advice import ADVICE_SCHEMA
from .rules import NAMES, SULFUR_THRESHOLD, UNITS, Policy
from .service import AdvisorService

logger = logging.getLogger(__name__)


def configured_service() -> AdvisorService:
    policy = Policy(
        interval_minutes=int(os.getenv("ADVISOR_INTERVAL_MINUTES", "30")),
        clock=os.getenv("ADVISOR_CLOCK", "replay"),  # type: ignore[arg-type]
    )
    return AdvisorService(SessionLocal, policy)


# Инструменты, которые платформа открывает советнику: запуск агентов защиты и производства.
ADVISOR_TOOLS = ["list_agents", "run_agent"]


def task_for(run: dict[str, Any]) -> str:
    """Постановка задачи советнику: только причины запуска и время данных.

    Роль, порядок работы и правила записаны в системном промпте советника (ИИ-сервис,
    agents/seed.py) и здесь не повторяются. Данные запуска передаются отдельно полем input.
    """
    lines = [
        f"- {reason.get('reason', '')}. {reason.get('consequence', '')}".rstrip()
        for reason in run["reasons"]
    ]
    at = run["snapshot"].get("at") or "—"
    return f"Время данных: {at}. Причины запуска:\n" + "\n".join(lines)


def input_for(run: dict[str, Any]) -> dict[str, Any]:
    """Исходные данные запуска: их дословно получают агенты, запущенные советником.

    Показания записаны компактно — время и значение без служебных полей: история за два
    часа по восьми датчикам иначе занимает десятки тысяч символов контекста агента.
    """
    snapshot = run["snapshot"]
    return {
        "reasons": run["reasons"],
        "at": snapshot.get("at"),
        "unit": snapshot.get("unit"),
        "sulfur_level": snapshot.get("sulfur_level"),
        "sulfur_threshold": SULFUR_THRESHOLD,
        # Названия и единицы кодов: без них в совете появляются числа без единиц.
        "parameters": {code: {"name": NAMES[code], "unit": UNITS.get(code)} for code in NAMES},
        "latest": {
            code: {"at": reading["at"], "value": reading["value"]}
            for code, reading in snapshot.get("readings", {}).items()
        },
        "history": {
            code: [[reading["at"], reading["value"]] for reading in readings]
            for code, readings in snapshot.get("recent_history", {}).items()
        },
        "policy": snapshot.get("policy"),
    }


# Состояния запуска, при которых советник работает и агрегатор держит темп реального времени.
# Запуск с неизвестным исходом («unknown») флаг не держит: советник уже не работает.
WORKING = ("queued", "sending", "running")

REALTIME_RESYNC_S = 30.0
"""Период повторной установки флага: агрегатор после перезапуска флага не помнит."""

REALTIME_RETRY_S = 10.0
"""Пауза после отказа агрегатора: недоступный агрегатор не должен задерживать каждый шаг
работника на время ожидания ответа."""


class RealtimeFlag:
    """Флаг реального времени агрегатора: включён, пока советник работает.

    Состояние отправляется при изменении и повторно раз в период. Отказ агрегатора советника
    не останавливает: флаг будет установлен следующей попыткой.
    """

    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client
        self.sent: bool | None = None
        """Последнее принятое агрегатором состояние; пусто — неизвестно или отказ."""
        self.sent_at = -REALTIME_RESYNC_S

    async def sync(self, enabled: bool) -> None:
        elapsed = time.monotonic() - self.sent_at
        if enabled == self.sent and elapsed < REALTIME_RESYNC_S:
            return
        if self.sent is None and elapsed < REALTIME_RETRY_S:
            return
        try:
            response = await self.client.put("/api/realtime", json={"enabled": enabled})
            response.raise_for_status()
        except httpx.HTTPError as error:
            logger.warning("Флаг реального времени агрегатора не установлен: %s", error)
            self.sent = None
        else:
            self.sent = enabled
        self.sent_at = time.monotonic()


class AdvisorWorker:
    def __init__(
        self,
        service: AdvisorService,
        client: httpx.AsyncClient,
        realtime: RealtimeFlag | None = None,
    ) -> None:
        self.service = service
        self.client = client
        self.realtime = realtime

    async def step(self) -> None:
        await asyncio.to_thread(self.service.process)
        run = await asyncio.to_thread(self.service.active_run)
        if run is None:
            await asyncio.to_thread(self.service.reserve)
            run = await asyncio.to_thread(self.service.active_run)
        if self.realtime is not None:
            await self.realtime.sync(run is not None and run["status"] in WORKING)
        if run is None:
            return
        if run["status"] == "queued":
            claimed = await asyncio.to_thread(self.service.claim, run["id"])
            if claimed is not None:
                await self._send(claimed)
        elif run["session_id"]:
            await self._poll(run)
        else:
            # После потери ответа или перезапуска нельзя вслепую повторять POST:
            # сессия могла уже запуститься. Ищем её по уникальному заголовку.
            await self._reconcile(run)

    async def _send(self, run: dict[str, Any]) -> None:
        payload: dict[str, Any] = {
            "kind": "agent",
            "title": f"24-2000 / {run['id']}",
            "task": task_for(run),
            "input": input_for(run),
            "resultSchema": ADVICE_SCHEMA,
            "tools": ADVISOR_TOOLS,
            "agent": os.getenv("ADVISOR_AGENT_ID") or "advisor",
        }
        try:
            response = await self.client.post("/api/sessions", json=payload)
            if 400 <= response.status_code < 500:
                await asyncio.to_thread(
                    self.service.update_run,
                    run["id"],
                    "failed",
                    error=f"Отказ ИИ: {response.status_code} {response.text[:300]}",
                )
                return
            response.raise_for_status()
            await self._accept(run["id"], response.json())
        except httpx.ConnectError as error:
            await asyncio.to_thread(
                self.service.update_run,
                run["id"],
                "failed",
                error=f"Соединение с ИИ-сервисом не установлено: {error}",
            )
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as error:
            await asyncio.to_thread(
                self.service.update_run,
                run["id"],
                "unknown",
                error=f"Исход отправки неизвестен; повтор отключён: {error}",
            )

    async def _accept(self, run_id: str, body: dict[str, Any]) -> None:
        session_id = body["id"]
        if not isinstance(session_id, str) or not session_id:
            raise ValueError("ИИ-сервис не вернул идентификатор сессии")
        status = body.get("status")
        await asyncio.to_thread(
            self.service.update_run,
            run_id,
            status if status in ("completed", "failed") else "running",
            session_id=session_id,
            result=body.get("result"),
            error=body.get("failureMessage"),
        )

    async def _poll(self, run: dict[str, Any]) -> None:
        response = await self.client.get(f"/api/sessions/{run['session_id']}")
        response.raise_for_status()
        await self._accept(run["id"], response.json())

    async def _reconcile(self, run: dict[str, Any]) -> None:
        response = await self.client.get("/api/sessions", params={"kind": "agent"})
        response.raise_for_status()
        matches = [
            s for s in response.json()["sessions"] if s.get("title") == f"24-2000 / {run['id']}"
        ]
        if len(matches) == 1:
            await self._accept(run["id"], matches[0])
        else:
            await asyncio.to_thread(
                self.service.update_run,
                run["id"],
                "unknown",
                error="Сессия не найдена однозначно. Проверьте ИИ-сервис; новые запуски удержаны.",
            )


async def run_worker(service: AdvisorService) -> None:
    async with (
        httpx.AsyncClient(base_url=ai_service_url(), timeout=15) as client,
        httpx.AsyncClient(base_url=aggregator_url(), timeout=3) as aggregator,
    ):
        worker = AdvisorWorker(service, client, RealtimeFlag(aggregator))
        while True:
            try:
                await worker.step()
            except Exception:
                logger.exception("Не удалось обработать события советника; журнал сохранён")
            await asyncio.sleep(2)
