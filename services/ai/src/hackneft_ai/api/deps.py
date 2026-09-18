"""Доступ обработчиков запросов к службам и передача отказов клиенту."""

from typing import Annotated

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse

from ..errors import ServiceError
from .container import Services


def get_services(request: Request) -> Services:
    services = request.app.state.services
    if not isinstance(services, Services):
        raise RuntimeError("Службы сервиса не запущены")
    return services


ServicesDep = Annotated[Services, Depends(get_services)]


def install_error_handlers(app: FastAPI) -> None:
    """Отказ службы передаётся кодом состояния и телом `{statusCode, error, message}`, как в
    API xip; дополнительные поля отказа — например, перечень сессий — идут рядом."""

    @app.exception_handler(ServiceError)
    async def service_error(_request: Request, error: ServiceError) -> JSONResponse:
        return JSONResponse(
            status_code=error.status_code,
            content={
                "statusCode": error.status_code,
                "error": error.error,
                "message": error.message,
                **error.extra,
            },
        )
