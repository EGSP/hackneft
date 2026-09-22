"""Зависимости ядра.

Это то, чего ядру не хватает для работы: обратиться к модели, узнать доступные инструменты,
записать событие. Протоколы объявлены здесь, в ядре, а не там, где находятся реализации:
направление зависимости обратно обычному — не сервис предоставляет ядру возможности, а ядро
выставляет требования, которые сервис удовлетворяет.
"""

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from hackneft_common.ai import RequestSnapshotContent, SessionEvent

from .messages import AgentMessage, ModelReply
from .tool import AnyAgentTool, ToolResult, ToolSpec


class ModelClient(Protocol):
    """Обращение к модели. Реализация отвечает за транспорт, авторизацию и повторы.

    Отказ провайдера объявляется исключением `ModelFailure`.
    """

    async def complete(
        self,
        messages: Sequence[AgentMessage],
        tools: Sequence[ToolSpec],
        *,
        result_schema: Mapping[str, Any] | None = None,
        reasoning_effort: str | None = None,
    ) -> ModelReply:
        """`result_schema` требует ответ объектом JSON по схеме (формат ответа провайдера);
        `reasoning_effort` — объём рассуждения, `none` выключает его."""
        ...


class ToolRegistry(Protocol):
    """Реестр инструментов, доступных агенту в текущем ходе."""

    @property
    def specs(self) -> Sequence[ToolSpec]: ...

    @property
    def names(self) -> Sequence[str]: ...

    def find(self, name: str) -> AnyAgentTool | None: ...


class Journal(Protocol):
    """Журнал сессии.

    Запись возвращает событие с проставленным порядковым номером — по нему подписчики
    понимают, что они пропустили при обрыве связи.
    """

    async def append(self, event: SessionEvent) -> SessionEvent: ...


class RequestSnapshots(Protocol):
    """Хранилище снимков постоянной части запроса.

    Запись возвращает идентификатор снимка; одинаковое содержимое получает один и тот же
    идентификатор, поэтому ход с неизменным набором новой записи не порождает.
    """

    async def save(self, snapshot: RequestSnapshotContent) -> str: ...


@dataclass(frozen=True, slots=True)
class ObservedToolCall:
    """Вызов инструмента в том виде, в каком он предъявляется наблюдателю.

    Предъявляется до того, как имя сверено с набором, а аргументы разобраны: отказ разбора
    наблюдается наравне с отказом исполнения.
    """

    call_id: str
    name: str
    raw_arguments: str
    step: int
    batch_size: int
    batch_index: int


class ToolObserver(Protocol):
    """Наблюдение за вызовом инструмента.

    Объявлено требованием ядра, а не встроено в цикл, потому что приёмник наблюдений — дело
    внешней стороны: ядро не зависит от OpenTelemetry. Наблюдение охватывает вызов целиком,
    а не только исполнение инструмента: неверный вызов — несуществующее имя, неразобранные
    аргументы, несоответствие схеме — говорит о качестве описаний инструментов.
    """

    async def observe(
        self, call: ObservedToolCall, run: Callable[[], Awaitable[ToolResult]]
    ) -> ToolResult: ...


class NoToolObserver:
    """Наблюдатель, ничего не делающий."""

    async def observe(
        self, call: ObservedToolCall, run: Callable[[], Awaitable[ToolResult]]
    ) -> ToolResult:
        return await run()


@dataclass(frozen=True, slots=True)
class TurnDeps:
    """Зависимости одного хода, собранные сервисом."""

    model: ModelClient
    tools: ToolRegistry
    journal: Journal
    snapshots: RequestSnapshots
    observer: ToolObserver
