"""Один работник: сначала все проверки, затем максимум одна сессия советника."""

import asyncio
import json
import logging
import os
from typing import Any

import httpx

from hackneft_platform.config import ai_service_url
from hackneft_platform.db import SessionLocal

from .rules import Policy
from .service import AdvisorService

logger = logging.getLogger(__name__)


def configured_service() -> AdvisorService:
    policy = Policy(
        interval_minutes=int(os.getenv("ADVISOR_INTERVAL_MINUTES", "30")),
        clock=os.getenv("ADVISOR_CLOCK", "replay"),  # type: ignore[arg-type]
    )
    return AdvisorService(SessionLocal, policy)


def task_for(run: dict[str, Any]) -> str:
    return (
        "Ты советник оператора установки гидроочистки 24-2000. Рассмотри все причины "
        "вместе. Собери одну сводку по результатам дочерних агентов согласно карточке. "
        "Если вызов агентов недоступен, явно сообщи об этом и не имитируй результаты. "
        "Структура ответа: что изменилось; возможное влияние на качество; что проверить "
        "или сделать; когда оценить эффект. Не выдавай исторические диапазоны за "
        "технологические пределы. Не придумывай допустимые уставки. Новое значение "
        "измерения не доказывает действие оператора. ЛИМС относится ко времени отбора "
        "пробы, а не к текущему времени; ПАК и Q21 не взаимозаменяемы. Не заявляй "
        "причинный эффект как доказанный. Норма серы продукта — не более 10 мг/кг. "
        "При недостатке сведений назови их. Управление оборудованием не выполняй.\n"
        + json.dumps({"reasons": run["reasons"], "snapshot": run["snapshot"]}, ensure_ascii=False)
    )


class AdvisorWorker:
    def __init__(self, service: AdvisorService, client: httpx.AsyncClient) -> None:
        self.service = service
        self.client = client

    async def step(self) -> None:
        await asyncio.to_thread(self.service.process)
        run = await asyncio.to_thread(self.service.active_run)
        if run is None:
            await asyncio.to_thread(self.service.reserve)
            run = await asyncio.to_thread(self.service.active_run)
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
            "tools": [],
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
    async with httpx.AsyncClient(base_url=ai_service_url(), timeout=15) as client:
        worker = AdvisorWorker(service, client)
        while True:
            try:
                await worker.step()
            except Exception:
                logger.exception("Не удалось обработать события советника; журнал сохранён")
            await asyncio.sleep(2)
