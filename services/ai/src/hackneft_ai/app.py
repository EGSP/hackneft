"""Приложение FastAPI ИИ-сервиса."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import mcp, models, providers, sessions
from .api.container import start_services, stop_services
from .api.deps import install_error_handlers
from .config import AppConfig
from .telemetry.tracing import init_tracing, shutdown_tracing


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

    app = FastAPI(title="Hackneft AI", version="0.1.0", lifespan=lifespan)
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
    app.include_router(providers.router)
    app.include_router(mcp.router)

    @app.get("/health", tags=["health"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
