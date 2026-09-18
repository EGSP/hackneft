"""Состав контекста: из чего сложится запрос к модели, если отправить его сейчас.

Постоянная часть — промпт и описания инструментов — берётся из снимка, а переписка собирается
из журнала функцией `build_messages`, как и в цикле. Поэтому оценка расходится с настоящим
запросом только погрешностью подсчёта токенов, а не составом.
"""

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from hackneft_common.ai import (
    ContextItem,
    ContextSegment,
    ContextSegmentKey,
    RequestSnapshotContent,
    SessionContextResponse,
    SessionEvent,
    SnapshotTool,
)

from .build_messages import build_messages
from .messages import AssistantMessage, SystemMessage, ToolMessage, UserMessage
from .tokenizer import estimate_tokens, tokenizer_for

_MESSAGE_OVERHEAD = 4
"""Служебные токены шаблона чата на одно сообщение: метки роли и границ."""
_TOOL_OVERHEAD = 10
"""Служебные токены на описание инструмента: обёртка `function`, поля `type` и `name`."""
_CALL_OVERHEAD = 6
"""Служебные токены на вызов инструмента в ответе модели: идентификатор, метки."""

# Окна контекста по документации провайдеров. Сопоставление по вхождению, поэтому более
# длинное имя стоит раньше того, что входит в него целиком.
_KNOWN_WINDOWS: tuple[tuple[str, int], ...] = (
    ("aliceai-llm-flash", 65_536),
    ("aliceai-llm", 131_072),
    ("yandexgpt", 32_768),
    ("deepseek-v4", 1_048_576),
    ("qwen3-235b", 262_144),
    ("qwen3.6", 262_144),
    ("gpt-oss", 131_072),
)

_ASSUMED_WINDOW = 32_768
"""Окно для модели, которой нет в перечне: наименьшее из известных. Завышенная заполненность
предупреждает заранее, а заниженная скрывает приближение к пределу."""


def context_window_for(model: str) -> tuple[int, bool]:
    """Размер окна и признак того, что он известен, а не подставлен."""
    name = model.lower()
    for fragment, window in _KNOWN_WINDOWS:
        if fragment in name:
            return window, True
    return _ASSUMED_WINDOW, False


@dataclass(frozen=True, slots=True)
class ContextInput:
    model: str
    """Модель, чьим токенизатором ведётся оценка и чьё окно служит пределом."""
    snapshot: RequestSnapshotContent
    """Постоянная часть запроса: промпт, секции MCP и инструменты, включая терминальные."""
    events: Sequence[SessionEvent]


def measure_context(context: ContextInput) -> SessionContextResponse:
    profile = tokenizer_for(context.model)

    def count(text: str) -> int:
        return estimate_tokens(text, profile)

    window, known = context_window_for(context.model)
    snapshot = context.snapshot

    prompt = ContextItem(
        name="system_prompt", tokens=count(snapshot.prompt) + _MESSAGE_OVERHEAD, count=1
    )
    # Секции присоединяются к промпту через пустую строку — отсюда два токена сверх текста.
    instructions = [
        ContextItem(
            name=_section_title(section) or f"Секция {index + 1}",
            tokens=count(section) + 2,
            count=1,
        )
        for index, section in enumerate(snapshot.sections)
    ]

    def describe_tool(tool: SnapshotTool) -> ContextItem:
        schema = json.dumps(tool.parameters, ensure_ascii=False, separators=(",", ":"))
        return ContextItem(
            name=tool.name,
            tokens=count(schema) + count(tool.description) + _TOOL_OVERHEAD,
            count=1,
        )

    builtin = [describe_tool(tool) for tool in snapshot.tools if tool.source != "mcp"]
    external = [describe_tool(tool) for tool in snapshot.tools if tool.source == "mcp"]

    # Части упорядочены по убыванию: состав смотрят, чтобы найти, что занимает окно.
    segments = sorted(
        (
            part
            for part in (
                _segment("system_prompt", [prompt]),
                _segment("mcp_instructions", instructions),
                _segment("tools", builtin),
                _segment("mcp_tools", external),
                _segment("messages", _measure_messages(context.events, count)),
            )
            if part.tokens > 0
        ),
        key=lambda part: part.tokens,
        reverse=True,
    )

    return SessionContextResponse(
        model=context.model,
        window=window,
        window_source="known" if known else "assumed",
        tokenizer=profile.name,
        used=sum(part.tokens for part in segments),
        segments=segments,
    )


def _measure_messages(
    events: Sequence[SessionEvent], count: Callable[[str], int]
) -> list[ContextItem]:
    """Переписка по видам сообщений.

    Считается по массиву, собранному из журнала, а не по самим событиям: рассуждение модели
    в запрос не попадает, а вызову, оставшемуся без результата после прерывания,
    подставляется отказ, который модель тоже получит.
    """
    totals: dict[str, list[int]] = {
        "user": [0, 0],
        "assistant": [0, 0],
        "tool_calls": [0, 0],
        "tool_results": [0, 0],
    }

    def add(kind: str, tokens: int) -> None:
        totals[kind][0] += tokens
        totals[kind][1] += 1

    for message in build_messages("", events):
        match message:
            case SystemMessage():
                continue
            case UserMessage():
                add("user", count(message.content) + _MESSAGE_OVERHEAD)
            case ToolMessage():
                add("tool_results", count(message.content) + _MESSAGE_OVERHEAD)
            case AssistantMessage():
                if message.content != "" or not message.tool_calls:
                    add("assistant", count(message.content) + _MESSAGE_OVERHEAD)
                for call in message.tool_calls:
                    add("tool_calls", count(call.name) + count(call.raw_arguments) + _CALL_OVERHEAD)

    return [
        ContextItem(name=name, tokens=tokens, count=number)
        for name, (tokens, number) in totals.items()
        if number > 0
    ]


def _segment(key: ContextSegmentKey, items: list[ContextItem]) -> ContextSegment:
    return ContextSegment(
        key=key,
        tokens=sum(item.tokens for item in items),
        items=sorted(items, key=lambda item: item.tokens, reverse=True),
    )


def _section_title(section: str) -> str | None:
    """Заголовок секции — первая строка без разметки: `## Сервер MCP «…»`."""
    first = section.strip().split("\n")[0].lstrip("#").strip()
    return first or None
