"""Инструмент агента: единое описание для модели плюс функция исполнения.

Схема аргументов служит двум целям сразу — проверке входа и порождению JSON Schema для модели.
У собственного инструмента она задаётся моделью pydantic, и одно объявление заменяет два
рассогласовывающихся. У инструмента внешнего сервера схема уже является JSON Schema, и
обратного преобразования не существует, поэтому обе формы приводятся к общему типу
`ToolInput` — `model_input` для первой, `raw_input` для второй.
"""

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, cast

from pydantic import BaseModel, ValidationError

from hackneft_common.ai import ToolOutcome, ToolSource


@dataclass(frozen=True, slots=True)
class InputAccepted[T]:
    value: T


@dataclass(frozen=True, slots=True)
class InputRejected:
    problems: str


@dataclass(frozen=True, slots=True)
class ToolInput[T]:
    """Схема аргументов в форме, пригодной обеим целям.

    `json_schema` уходит модели, `check` проверяет пришедшее от неё. Разделение позволяет
    инструменту внешнего сервера предъявить полученную схему, ничего о ней не зная.
    """

    json_schema: dict[str, Any]
    check: Callable[[object], InputAccepted[T] | InputRejected]


@dataclass(frozen=True, slots=True)
class AgentTool[T]:
    name: str
    description: str
    """Текст, который видит модель. От его точности прямо зависит доля неверных вызовов."""
    input: ToolInput[T]
    execute: Callable[[T], Awaitable[object]]
    source: ToolSource = "builtin"


AnyAgentTool = AgentTool[Any]
"""Инструмент с произвольным типом входа — для реестров и обобщённых функций."""


class ToolFailure(Exception):
    """Отказ инструмента.

    Это штатный исход, а не дефект: он возвращается модели результатом вызова, чтобы та
    исправилась на следующем шаге. Поэтому текст пишется для модели как для адресата и
    содержит предписание, а не только констатацию.
    """

    def __init__(self, message: str, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint


def describe_cause(cause: object) -> str:
    """Текст произвольной причины отказа."""
    if isinstance(cause, BaseException):
        text = str(cause)
        return text if text != "" else type(cause).__name__
    return str(cause)


def tool_failure(summary: str, hint: str | None, cause: BaseException) -> ToolFailure:
    """Отказ инструмента из исключения: сводка, причина и подсказка модели.

    Обращение к базе данных либо к внешней службе способно отказать, и такой отказ должен
    дойти до модели штатным исходом с подсказкой, а не дефектом с текстом исключения.
    """
    return ToolFailure(f"{summary}: {describe_cause(cause)}", hint)


@dataclass(frozen=True, slots=True)
class ToolResult:
    """Результат вызова инструмента — единый тип для всех классов исхода."""

    kind: ToolOutcome
    content: str
    """Текст, уходящий модели сообщением роли `tool` и в журнал сессии. У отказа это объект с
    полями `error`, `message` и `hint`, построенный `format_tool_error`."""
    detail: str | None = None
    """Подробность исхода для наблюдения: текст отказа без обрамления, а у дефекта — текст
    исключения. Ни модели, ни в журнал сессии не уходит."""


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """Описание инструмента в форме, которую принимает OpenAI-совместимый API."""

    name: str
    description: str
    parameters: dict[str, Any]


def tool_spec(tool: AnyAgentTool) -> ToolSpec:
    return ToolSpec(name=tool.name, description=tool.description, parameters=tool.input.json_schema)


def format_tool_error(message: str, hint: str | None = None) -> str:
    """Единый вид ошибки, возвращаемой модели результатом вызова."""
    payload: dict[str, object] = {"error": True, "message": message}
    if hint is not None:
        payload["hint"] = hint
    return json.dumps(payload, ensure_ascii=False)


def json_schema_of(model: type[BaseModel]) -> dict[str, Any]:
    """JSON Schema модели аргументов без заголовков.

    Заголовки pydantic выводит из имён классов и полей; модели они ничего не сообщают, а
    место в контексте занимают на каждом шаге.
    """
    return cast(dict[str, Any], _strip_titles(model.model_json_schema()))


def describe_validation_error(error: ValidationError) -> str:
    """Структурное описание несоответствия: путь до поля и суть расхождения."""
    problems = []
    for issue in error.errors(include_url=False):
        path = ".".join(str(part) for part in issue["loc"]) or "(корень)"
        problems.append(f"{path}: {issue['msg']}")
    return "; ".join(problems)


def model_input[M: BaseModel](model: type[M]) -> ToolInput[M]:
    """Схема собственного инструмента.

    Проверка моделью даёт структурное описание несоответствия — путь до поля и суть
    расхождения, — которое переводится в подсказку модели почти без обработки. Схема
    вычисляется при объявлении инструмента, а не на каждый ход.
    """

    def check(raw: object) -> InputAccepted[M] | InputRejected:
        try:
            return InputAccepted(model.model_validate(raw))
        except ValidationError as error:
            return InputRejected(describe_validation_error(error))

    return ToolInput(json_schema=json_schema_of(model), check=check)


def raw_input(json_schema: dict[str, Any]) -> ToolInput[dict[str, Any]]:
    """Схема, полученная извне готовой JSON Schema.

    Проверка сводится к требованию объекта. Внешний сервер проверяет вход сам и возвращает
    описание несоответствия — оно и попадёт модели отказом инструмента.
    """

    def check(raw: object) -> InputAccepted[dict[str, Any]] | InputRejected:
        if not isinstance(raw, dict):
            return InputRejected("(корень): ожидается объект аргументов")
        return InputAccepted(cast(dict[str, Any], raw))

    return ToolInput(json_schema=json_schema, check=check)


_SCHEMA_KEYS = ("items", "additionalProperties", "not", "if", "then", "else", "contains")
_SCHEMA_LISTS = ("anyOf", "allOf", "oneOf", "prefixItems")
_SCHEMA_MAPS = ("properties", "$defs", "patternProperties")


def _strip_titles(schema: object) -> object:
    """Удаляет ключи `title` у схем, не трогая свойств с именем `title`."""
    if not isinstance(schema, dict):
        return schema
    result: dict[str, object] = {}
    for key, value in schema.items():
        if key == "title":
            continue
        if key in _SCHEMA_MAPS and isinstance(value, dict):
            result[key] = {name: _strip_titles(item) for name, item in value.items()}
        elif key in _SCHEMA_LISTS and isinstance(value, list):
            result[key] = [_strip_titles(item) for item in value]
        elif key in _SCHEMA_KEYS:
            result[key] = _strip_titles(value)
        else:
            result[key] = value
    return result
