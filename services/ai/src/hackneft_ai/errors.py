"""Отказы служб, которые API передаёт клиенту кодом состояния HTTP."""

from collections.abc import Mapping


class ServiceError(Exception):
    status_code = 400
    error = "Bad Request"

    def __init__(self, message: str, *, extra: Mapping[str, object] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.extra = dict(extra or {})


class BadRequestError(ServiceError):
    pass


class NotFoundError(ServiceError):
    status_code = 404
    error = "Not Found"


class ConflictError(ServiceError):
    status_code = 409
    error = "Conflict"
