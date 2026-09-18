"""Состав контекста сессии: из чего сложится запрос к модели, если отправить его сейчас.

Числа — оценка, а не показания провайдера. Провайдер сообщает расход только суммой по всему
запросу и только после обращения, тогда как показать нужно доли — промпт, инструменты,
переписку — и до следующего хода.
"""

from typing import Literal

from .base import ApiModel

ContextSegmentKey = Literal["system_prompt", "mcp_instructions", "tools", "mcp_tools", "messages"]


class ContextItem(ApiModel):
    """Составляющая части: инструмент по имени либо вид сообщений.

    Для сообщений имя — один из видов `user`, `assistant`, `tool_calls`, `tool_results`.
    """

    name: str
    tokens: int
    count: int
    """Сколько элементов слито в составляющую: сообщений одного вида, секций."""


class ContextSegment(ApiModel):
    key: ContextSegmentKey
    tokens: int
    items: list[ContextItem]


class SessionContextResponse(ApiModel):
    model: str
    """Модель, для которой сделана оценка: от неё зависят и окно, и соотношения."""
    window: int
    """Размер окна контекста модели в токенах."""
    window_source: Literal["known", "assumed"]
    """`known` — модель есть в перечне сервиса; `assumed` — нет, и подставлено осторожное
    значение."""
    tokenizer: str
    """Семейство токенизатора, чьими соотношениями получена оценка."""
    used: int
    segments: list[ContextSegment]
    """Части по убыванию размера; составляющие внутри части — так же."""
