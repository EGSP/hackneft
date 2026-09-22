"""Проверка схемы итога при создании сессии.

Схему задаёт вызывающая сторона; сервис не знает её смысла. Объект, полученный от модели,
проверяется по ней в конце хода (core/structured.py).
"""

from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from ..errors import BadRequestError


def check_result_schema(schema: dict[str, Any]) -> None:
    """Отклоняет некорректную схему при создании сессии, а не в конце работы агента."""
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as error:
        raise BadRequestError(f"Схема итога некорректна: {error.message}") from error
    if schema.get("type") != "object":
        raise BadRequestError("Схема итога должна описывать объект: type = object")
