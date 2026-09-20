"""Приложение FastAPI агрегатора.

Веб-интерфейс собран так же, как у ИИ-сервиса: статические файлы без сборки, данные берутся
запросами к собственному API. Поля JSON именуются в camelCase — по тому же правилу, что и в
ИИ-сервисе; snake_case остаётся только в телах запросов к платформе, где так именует поля она.
"""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from os import PathLike
from pathlib import Path

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.types import Scope

from .config import AggregatorConfig
from .runner import SimulationRunner

_UI_DIR = Path(__file__).parent / "ui"


class _RevalidatedFiles(StaticFiles):
    """Статические файлы, которые браузер сверяет с сервером при каждом обращении.

    Без заголовка `Cache-Control` браузер кэширует файлы по своей оценке, и после обновления
    сервиса страница продолжала бы работать со старым скриптом.
    """

    def file_response(
        self,
        full_path: PathLike[str] | str,
        stat_result: os.stat_result,
        scope: Scope,
        status_code: int = 200,
    ) -> Response:
        response = super().file_response(full_path, stat_result, scope, status_code)
        response.headers["Cache-Control"] = "no-cache"
        return response


class SettingsUpdate(BaseModel):
    """Изменение настроек темпа. Переданы могут быть оба значения или одно из них."""

    intervalMinutes: float | None = Field(default=None, gt=0)
    stepMinutes: float | None = Field(default=None, gt=0)


class CursorUpdate(BaseModel):
    cursor: datetime


def create_app(config: AggregatorConfig) -> FastAPI:
    runner = SimulationRunner(config)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await runner.start()
        try:
            yield
        finally:
            await runner.stop()

    app = FastAPI(
        title="Hackneft Aggregator",
        version="0.1.0",
        summary="Симуляция потока телеметрии: чтение источников и отправка показаний на платформу",
        lifespan=lifespan,
    )

    @app.get("/health", tags=["health"], summary="Проверка работы сервиса")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/state", summary="Состояние симуляции")
    async def state() -> dict[str, object]:
        return runner.snapshot()

    @app.post("/api/control/resume", summary="Начать или продолжить отправку")
    async def resume() -> dict[str, object]:
        await runner.resume()
        return runner.snapshot()

    @app.post("/api/control/pause", summary="Приостановить отправку")
    async def pause() -> dict[str, object]:
        await runner.pause()
        return runner.snapshot()

    @app.patch("/api/settings", summary="Изменить интервал опроса и шаг курсора")
    async def settings(payload: SettingsUpdate) -> dict[str, object]:
        if payload.intervalMinutes is None and payload.stepMinutes is None:
            raise HTTPException(status_code=400, detail="Не передано ни одной настройки")
        await runner.update_settings(payload.intervalMinutes, payload.stepMinutes)
        return runner.snapshot()

    @app.put("/api/cursor", summary="Установить положение курсора")
    async def set_cursor(payload: CursorUpdate) -> dict[str, object]:
        cursor = payload.cursor
        if cursor.tzinfo is not None:
            # Отметки времени в источнике пояса не несут; пояс из запроса приводится к UTC
            # и отбрасывается, иначе сравнение с отметками источника было бы невозможно.
            cursor = cursor.astimezone(tz=None).replace(tzinfo=None)
        await runner.set_cursor(cursor)
        return runner.snapshot()

    @app.post("/api/cursor/reset", summary="Вернуть курсор к начальной дате")
    async def reset_cursor() -> dict[str, object]:
        await runner.reset_cursor()
        return runner.snapshot()

    @app.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        return RedirectResponse("/ui/")

    app.mount("/ui", _RevalidatedFiles(directory=_UI_DIR, html=True), name="ui")

    return app
