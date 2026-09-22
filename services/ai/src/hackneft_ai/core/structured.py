"""Итоговый ответ хода, запрашиваемый инструментом завершения.

Модель не пишет итог аргументом `attempt_completion`: она только объявляет, что работа
закончена. Инструмент дописывает в диалог запрос итогового ответа и ещё раз обращается к модели
по всей истории — так, как нужно вызывающей стороне: обычным текстом либо объектом по JSON
Schema, переданной форматом ответа провайдера. Модель отвечает как обычно, с полным контекстом
своей работы, а строгий формат требуется только от этого последнего обращения: совмещать
вызовы инструментов и формат ответа в одном обращении умеют не все модели.

Объект проверяется по схеме; при несоответствии модели возвращается перечень ошибок, и она
исправляет ответ.
"""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from jsonschema import Draft202012Validator
from pydantic import JsonValue

from .budget import model_step
from .errors import ModelFailure, TurnError
from .messages import AgentMessage, AssistantMessage, UserMessage
from .requirements import TurnDeps

STRUCTURED_ATTEMPTS = 3
"""Сколько раз модель может исправить ответ, не соответствующий схеме."""


class ResultSchemaMismatch(TurnError):
    """Модель так и не вернула объект, соответствующий схеме итога."""

    def __init__(self, problems: Sequence[str]) -> None:
        self.problems = list(problems)
        self.message = f"Итог не оформлен по схеме за {STRUCTURED_ATTEMPTS} попытки: " + "; ".join(
            problems
        )
        super().__init__(self.message)


def final_request(schema: Mapping[str, Any] | None) -> str:
    """Запрос итогового ответа — результат вызова `attempt_completion`."""
    if schema is None:
        return (
            "Работа завершена. Теперь дай итоговый ответ обычным текстом — ровно то, что "
            "нужно адресату, без рассказа о своей работе. Инструменты больше не вызывай."
        )
    return (
        "Работа завершена. Теперь дай итоговый ответ одним объектом JSON строго по схеме ниже, "
        "без текста вокруг. Инструменты больше не вызывай.\n\n"
        f"```json\n{json.dumps(schema, ensure_ascii=False)}\n```"
    )


def result_problems(schema: Mapping[str, Any], value: object) -> list[str]:
    """Несоответствия объекта схеме в виде, понятном модели: путь к полю и причина."""
    validator = Draft202012Validator(dict(schema))
    problems = []
    for error in sorted(validator.iter_errors(value), key=lambda e: list(e.absolute_path)):
        path = "/".join(str(part) for part in error.absolute_path) or "(корень)"
        problems.append(f"{path}: {error.message}")
    return problems


def parse_reply(text: str) -> object:
    """Объект JSON из ответа модели.

    Часть моделей оборачивает ответ в блок кода даже при заданном формате ответа, поэтому
    ограждение снимается. Ошибка разбора объявляется `ValueError`.
    """
    body = text.strip()
    if body.startswith("```"):
        body = body.split("\n", 1)[1] if "\n" in body else ""
        body = body.rsplit("```", 1)[0]
    return json.loads(body)


@dataclass(frozen=True, slots=True)
class FinalAnswer:
    text: str
    """Итоговое сообщение хода. При схеме — объект, размеченный блоком кода JSON."""
    data: JsonValue
    """Объект по схеме. Пусто, если схема не задана."""
    steps: int
    """Сколько обращений к модели потребовалось."""


async def final_answer(
    conversation: Sequence[AgentMessage],
    schema: Mapping[str, Any] | None,
    *,
    first_step: int,
    max_steps: int,
    provider: str,
    model: str,
    snapshot_id: str,
    deps: TurnDeps,
) -> FinalAnswer:
    """Последнее обращение к модели за итогом. Каждое обращение записывается шагом журнала.

    `conversation` уже оканчивается запросом итогового ответа. Инструменты модели не
    передаются, рассуждение выключено: ход закончен, выводы сделаны в ходе работы, и
    отвечать нужно сразу — повторное рассуждение над всей историей лишь тратит бюджет вывода
    и рискует зациклиться.
    """
    messages = list(conversation)
    problems: list[str] = []
    attempts = 1 if schema is None else STRUCTURED_ATTEMPTS
    for attempt in range(attempts):
        answered = await model_step(
            messages,
            [],
            step=first_step + attempt,
            max_steps=max_steps,
            provider=provider,
            model=model,
            snapshot_id=snapshot_id,
            deps=deps,
            result_schema=schema,
            reasoning=False,
        )
        reply = answered.reply
        content = reply.content.strip()
        if schema is None:
            if content == "":
                raise ModelFailure("Модель не дала итогового ответа после завершения работы")
            return FinalAnswer(content, None, attempt + 1)
        try:
            value: Any = parse_reply(content)
        except ValueError as error:
            problems = [f"ответ не является объектом JSON: {error}"]
        else:
            problems = result_problems(schema, value)
            if not problems:
                text = f"```json\n{json.dumps(value, ensure_ascii=False, indent=2)}\n```"
                return FinalAnswer(text, value, attempt + 1)
        messages = [
            *messages,
            *answered.context,
            AssistantMessage(reply.content),
            UserMessage(
                "Ответ не соответствует схеме: "
                + "; ".join(problems)
                + ". Исправь и верни только объект JSON по схеме."
            ),
        ]
    raise ResultSchemaMismatch(problems)
