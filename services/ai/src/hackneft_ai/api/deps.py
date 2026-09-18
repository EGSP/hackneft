"""Доступ обработчиков запросов к службам и передача отказов клиенту."""

from typing import Annotated, Any

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from hackneft_common.ai import ErrorResponse

from ..errors import ServiceError
from .container import Services


def get_services(request: Request) -> Services:
    services = request.app.state.services
    if not isinstance(services, Services):
        raise RuntimeError("Службы сервиса не запущены")
    return services


ServicesDep = Annotated[Services, Depends(get_services)]

_ERROR_DESCRIPTIONS = {
    400: "Запрос отклонён службой: неверные значения или недостающие данные",
    404: "Запись не найдена",
    409: "Запрос противоречит текущему состоянию",
}


def errors(
    *codes: int, conflict: type[BaseModel] = ErrorResponse
) -> dict[int | str, dict[str, Any]]:
    """Отказы маршрута для схемы OpenAPI.

    Отказ 409 с дополнительными полями описывается собственной моделью тела в `conflict`.
    """
    return {
        code: {
            "model": conflict if code == 409 else ErrorResponse,
            "description": _ERROR_DESCRIPTIONS[code],
        }
        for code in codes
    }


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
