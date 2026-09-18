"""Терминальные инструменты.

Признак «ответ без вызовов инструментов» как условие завершения недостаточен по двум
противоположным причинам. Модель останавливается раньше времени, объявив словами, что
собирается делать дальше, — формально ход завершён, фактически задача брошена. И модель не
останавливается тогда, когда следовало: задаёт уточняющий вопрос обычным текстом, а программа
продолжает цикл, и модель отвечает сама себе.

Оба инструмента ничего не исполняют. Они существуют затем, чтобы модель объявила, чем
закончила, явно. Поэтому и обрабатываются они в цикле, а не в реестре инструментов: реестр
предоставляет действия, а эти два — способ завершиться.
"""

import json
from typing import Literal

from pydantic import BaseModel, Field

from .tool import ToolSpec, json_schema_of

ATTEMPT_COMPLETION = "attempt_completion"
ASK_USER = "ask_user"

CompletionMode = Literal["chat", "task"]
"""Дисциплина завершения хода.

`chat` — доступны оба инструмента, и терминальный вызов завершает ход; сессия при этом
продолжается и ожидает следующего сообщения. `task` — доступен только `attempt_completion`,
потому что спрашивать некого, и тот же вызов завершает сессию целиком.
"""

TurnFinish = Literal["completion", "question", "plain"]
"""Чем завершился ход: объявлением итога, вопросом либо обычным текстом без терминального вызова."""


class _CompletionArguments(BaseModel):
    text: str = Field(
        min_length=1,
        description="Итоговый ответ целиком. Это единственный текст, который увидит адресат",
    )


class _QuestionArguments(BaseModel):
    text: str = Field(
        min_length=1,
        description="Вопрос пользователю, на который нужен ответ для продолжения",
    )


_ATTEMPT_COMPLETION_SPEC = ToolSpec(
    name=ATTEMPT_COMPLETION,
    description=(
        "Завершает работу и передаёт итоговый ответ. Вызывай, когда задача выполнена. "
        "Не описывай предстоящие шаги словами вместо их выполнения."
    ),
    parameters=json_schema_of(_CompletionArguments),
)

_ASK_USER_SPEC = ToolSpec(
    name=ASK_USER,
    description=(
        "Задаёт уточняющий вопрос и передаёт слово пользователю. Вызывай, когда без ответа "
        "продолжать нельзя. Обычным текстом вопрос не задавай — он останется без ответа."
    ),
    parameters=json_schema_of(_QuestionArguments),
)


def terminal_specs(mode: CompletionMode) -> tuple[ToolSpec, ...]:
    if mode == "chat":
        return (_ATTEMPT_COMPLETION_SPEC, _ASK_USER_SPEC)
    return (_ATTEMPT_COMPLETION_SPEC,)


def is_terminal(name: str, mode: CompletionMode) -> bool:
    if name == ATTEMPT_COMPLETION:
        return True
    return name == ASK_USER and mode == "chat"


def terminal_text(raw_arguments: str) -> str:
    """Текст из аргументов терминального вызова.

    Разбор может не удаться: аргументы приходят строкой от модели. Отказ здесь не должен
    терять ход целиком, поэтому при неудаче возвращается пустая строка, а вызывающая сторона
    подставляет обычный текст ответа.
    """
    try:
        parsed: object = {} if raw_arguments == "" else json.loads(raw_arguments)
    except json.JSONDecodeError:
        return ""
    if not isinstance(parsed, dict):
        return ""
    value = parsed.get("text")
    return value.strip() if isinstance(value, str) else ""
