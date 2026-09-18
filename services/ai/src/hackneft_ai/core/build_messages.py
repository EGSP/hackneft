"""Сборка массива сообщений для модели из журнала событий.

Функция чистая: журнал и системный промпт на входе, массив сообщений на выходе, никаких
обращений к базе и внешним системам. Это даёт два свойства. Во-первых, вся логика управления
контекстом проверяется на зафиксированных журналах без обращения к модели. Во-вторых, по
журналу восстанавливается ровно тот запрос, который был отправлен.

Сведения об ответах модели — расход и рассуждение — и итоги хода в диалог не попадают: модель
не ждёт их обратно.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field

from hackneft_common.ai import (
    AssistantMessageEvent,
    AssistantNoteEvent,
    SessionEvent,
    ToolCallEvent,
    ToolResultEvent,
    UserMessageEvent,
)

from .messages import AgentMessage, AgentToolCall, AssistantMessage, SystemMessage, ToolMessage
from .messages import UserMessage as UserChatMessage
from .tool import format_tool_error

_INTERRUPTED_CALL = format_tool_error(
    "Вызов не был завершён: ход прерван.",
    "Если действие всё ещё нужно, повтори вызов.",
)


@dataclass(slots=True)
class _Result:
    call_id: str
    content: str
    taken: bool = False


@dataclass(slots=True)
class _PendingStep:
    step: int
    note: str = ""
    calls: list[AgentToolCall] = field(default_factory=list)
    results: list[_Result] = field(default_factory=list)
    """Результаты в порядке поступления. Сопоставление идёт по идентификатору, а при его
    повторе — по порядку: часть моделей возвращает вместо идентификатора имя инструмента, и
    тогда все вызовы одного инструмента в шаге неразличимы по нему."""

    def take_result(self, call_id: str) -> str | None:
        entry = next((r for r in self.results if not r.taken and r.call_id == call_id), None)
        if entry is None:
            entry = next((r for r in self.results if not r.taken), None)
        if entry is None:
            return None
        entry.taken = True
        return entry.content


class _Builder:
    def __init__(self, system_prompt: str) -> None:
        self.messages: list[AgentMessage] = [SystemMessage(system_prompt)]
        self._pending: _PendingStep | None = None

    def step(self, number: int) -> _PendingStep:
        if self._pending is not None and self._pending.step != number:
            self.flush()
        if self._pending is None:
            self._pending = _PendingStep(number)
        return self._pending

    def flush(self) -> None:
        pending = self._pending
        if pending is None:
            return
        self._pending = None
        if not pending.calls:
            if pending.note != "":
                self.messages.append(AssistantMessage(pending.note))
            return
        self.messages.append(AssistantMessage(pending.note, tuple(pending.calls)))
        for call in pending.calls:
            # Вызов без результата остаётся после прерванного хода. Отправить его модели как
            # есть нельзя: API требует ответа на каждый вызов. Подставляется явный отказ —
            # так модель узнаёт, что действие не состоялось, и не считает его выполненным.
            content = pending.take_result(call.id)
            self.messages.append(
                ToolMessage(call.id, content if content is not None else _INTERRUPTED_CALL)
            )


def build_messages(system_prompt: str, events: Sequence[SessionEvent]) -> list[AgentMessage]:
    builder = _Builder(system_prompt)
    for event in events:
        match event:
            case UserMessageEvent():
                builder.flush()
                builder.messages.append(UserChatMessage(event.text))
            case AssistantNoteEvent():
                builder.step(event.step).note = event.text
            case ToolCallEvent():
                builder.step(event.step).calls.append(
                    AgentToolCall(event.call_id, event.name, event.raw_arguments)
                )
            case ToolResultEvent():
                builder.step(event.step).results.append(_Result(event.call_id, event.content))
            case AssistantMessageEvent():
                builder.flush()
                builder.messages.append(AssistantMessage(event.text))
            case _:
                pass
    builder.flush()
    return builder.messages
