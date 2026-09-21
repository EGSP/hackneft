"""Приложение FastAPI ИИ-сервиса."""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from os import PathLike
from pathlib import Path

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.types import Scope

from .api import agents, instructions, mcp, models, providers, sessions
from .api.container import start_services, stop_services
from .api.deps import install_error_handlers
from .config import AppConfig
from .telemetry.tracing import init_tracing, shutdown_tracing

_UI_DIR = Path(__file__).parent / "ui"

_DESCRIPTION = """
ИИ-сервис не зависит от предметной области: он запускает модели, ведёт сессии и предоставляет
инструменты. Поля JSON именуются в camelCase. Просмотр справочников доступен на странице `/ui/`.

## Порядок работы

1. Добавить карточку провайдера: `POST /api/providers`. Перечень его моделей возвращает
   `GET /api/providers/{provider_id}/models`.
2. Добавить модель в справочник: `POST /api/models`. Первая добавленная модель становится
   моделью по умолчанию.
3. При необходимости добавить карточку агента: `POST /api/agents`. Карточка задаёт
   системный промпт, закреплённые инструкции из `/api/instructions` и модель сессии. Пустые
   справочники агентов и инструкций заполняются при запуске значениями по умолчанию.
4. Создать сессию: `POST /api/sessions`. Идентификатор сессии возвращается в ответе. Поле
   `agent` создаёт сессию по карточке агента, поле `model` указывает модель идентификатором
   записи, идентификатором модели у провайдера либо синонимом.
   - `kind: "chat"` — сообщения передаются запросом `POST /api/sessions/{session_id}/messages`,
     число ходов не ограничено.
   - `kind: "agent"` — задача передаётся в поле `task`, ход начинается сразу, сессия
     завершается состоянием `completed` или `failed`. Поле `tools` сужает набор инструментов,
     поле `parentId` делает сессию дочерней.
5. Читать события журнала: `GET /api/sessions/{session_id}/events?after=<seq>` либо поток
   `GET /api/sessions/{session_id}/stream?after=<seq>`. Ответ на сообщение и итог агентской
   сессии приходят событиями, а не в ответе на запрос.

## Отказы

Отказ службы передаётся кодом 400, 404 или 409 и телом `{statusCode, error, message}`;
отдельные отказы добавляют к нему поля с подробностями. Ошибки разбора запроса возвращаются
кодом 422 в формате FastAPI.
"""

_TAGS = [
    {"name": "sessions", "description": "Сессии, журнал событий, поток SSE и прерывание ходов"},
    {
        "name": "providers",
        "description": "Справочник провайдеров моделей: Yandex и OpenAI-совместимые",
    },
    {"name": "models", "description": "Справочник моделей и проверка их доступности"},
    {
        "name": "agents",
        "description": "Справочник агентов: системный промпт, инструкции и модель сессии",
    },
    {"name": "instructions", "description": "Справочник инструкций для карточек агентов"},
    {"name": "mcp", "description": "Справочник подключений к серверам MCP"},
    {"name": "health", "description": "Служебная проверка"},
]


class _RevalidatedFiles(StaticFiles):
    """Статические файлы, которые браузер сверяет с сервером при каждом обращении.

    Без заголовка `Cache-Control` браузер кэширует файлы по своей оценке, и после обновления
    сервиса страница продолжала бы работать со старым скриптом. Сверка идёт по ETag, поэтому
    неизменённый файл повторно не передаётся.
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


def create_app(config: AppConfig) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        init_tracing(config.tracing)
        services = await start_services(config)
        app.state.services = services
        try:
            yield
        finally:
            await stop_services(services)
            shutdown_tracing()

    app = FastAPI(
        title="Hackneft AI",
        version="0.1.0",
        summary="Запуск моделей, сессии агентов и инструменты, в том числе серверов MCP",
        description=_DESCRIPTION,
        openapi_tags=_TAGS,
        # Идентификатор операции — имя обработчика: такие имена получают методы клиентов,
        # созданных по схеме. Имена обработчиков во всех разделах различны.
        generate_unique_id_function=lambda route: route.name,
        lifespan=lifespan,
    )
    # Методы перечисляются явно: запросы PATCH и DELETE браузер предваряет запросом OPTIONS, и
    # отсутствие метода в ответе на него приводит к отказу ещё до обращения к сервису.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(config.cors_origins),
        allow_methods=["GET", "HEAD", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Last-Event-ID"],
    )
    install_error_handlers(app)
    app.include_router(sessions.router)
    app.include_router(models.router)
    app.include_router(agents.router)
    app.include_router(instructions.router)
    app.include_router(providers.router)
    app.include_router(mcp.router)

    @app.get("/health", tags=["health"], summary="Проверка работы сервиса")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        return RedirectResponse("/ui/")

    # Страница просмотра справочников: статические файлы без сборки, данные берутся из API.
    app.mount("/ui", _RevalidatedFiles(directory=_UI_DIR, html=True), name="ui")

    return app
