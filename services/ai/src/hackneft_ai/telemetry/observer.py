"""Наблюдение за ходом спанами трассировки: вызовы инструментов и обращения к модели.

Обе реализации удовлетворяют требованиям ядра — `ToolObserver` и `ModelClient`, — поэтому
OpenTelemetry в ядро не проникает. Родительский контекст передаётся параметром, а не берётся
из текущего: так дерево спанов не зависит от того, в какой задаче выполняется код.
"""

import logging
from collections.abc import Awaitable, Callable, Sequence

from opentelemetry.context import Context
from opentelemetry.trace import Status, StatusCode

from ..core.errors import ModelFailure
from ..core.messages import AgentMessage, ModelReply
from ..core.requirements import ObservedToolCall
from ..core.tool import ToolResult, ToolSpec
from ..providers.base import ChatSettings, ModelProvider
from .attributes import (
    chat_request_attributes,
    chat_response_attributes,
    session_attributes,
    tool_call_attributes,
    tool_result_attributes,
)
from .tracing import tracer

logger = logging.getLogger(__name__)


class TracingToolObserver:
    """Спан на каждый вызов инструмента, включая сверку имени и разбор аргументов."""

    def __init__(self, session_id: str, parent: Context, capture: bool) -> None:
        self._session_id = session_id
        self._parent = parent
        self._capture = capture

    async def observe(
        self, call: ObservedToolCall, run: Callable[[], Awaitable[ToolResult]]
    ) -> ToolResult:
        span = tracer().start_span(f"execute_tool {call.name}", context=self._parent)
        if span.is_recording():
            span.set_attributes(
                {
                    **session_attributes(self._session_id),
                    **tool_call_attributes(call, self._capture),
                }
            )
        try:
            result = await run()
        except BaseException:
            # Прерывание хода не является отказом вызова, но оставленный без пометки спан
            # неотличим от спана, закрытого без исхода.
            span.set_attribute("hackneft.tool.outcome", "interrupted")
            span.end()
            raise

        span.set_attributes(tool_result_attributes(result, self._capture))
        if result.kind == "ok":
            span.set_status(Status(StatusCode.OK))
        else:
            message = result.detail or result.content
            span.set_status(Status(StatusCode.ERROR, message))
            if result.kind == "defect":
                # Текст исключения модели не уходит, и спан вместе с журналом сервиса остаётся
                # единственным местом, где он доступен.
                span.add_event(
                    "exception",
                    {"exception.type": "ToolDefect", "exception.message": message},
                )
                logger.error(
                    "сессия %s: дефект при вызове %s (шаг %d, вызов %s): %s",
                    self._session_id,
                    call.name,
                    call.step,
                    call.call_id,
                    message,
                )
        span.end()
        return result


class TracedModelClient:
    """Обращение к модели хода через провайдер — реализация зависимости ядра `ModelClient`."""

    def __init__(
        self,
        *,
        provider: ModelProvider,
        identifier: str,
        settings: ChatSettings,
        session_id: str,
        parent: Context,
        capture: bool,
    ) -> None:
        self._provider = provider
        self._identifier = identifier
        self._settings = settings
        self._session_id = session_id
        self._parent = parent
        self._capture = capture

    async def complete(
        self, messages: Sequence[AgentMessage], tools: Sequence[ToolSpec]
    ) -> ModelReply:
        span = tracer().start_span(f"chat {self._identifier}", context=self._parent)
        if span.is_recording():
            span.set_attributes(
                {
                    **session_attributes(self._session_id),
                    **chat_request_attributes(
                        provider=self._provider.name,
                        model_name=self._identifier,
                        model_uri=self._provider.model_uri(self._identifier),
                        temperature=self._settings.temperature,
                        messages=messages,
                        tool_count=len(tools),
                        capture=self._capture,
                    ),
                }
            )
        # Спан закрывается при любом исходе, включая отмену хода: отмена доходит до запроса к
        # модели, и провайдер прекращает генерацию.
        try:
            reply = await self._provider.complete(self._identifier, messages, tools, self._settings)
        except ModelFailure as failure:
            span.set_status(Status(StatusCode.ERROR, failure.message))
            span.end()
            raise
        except BaseException:
            span.end()
            raise
        if span.is_recording():
            span.set_attributes(chat_response_attributes(reply, self._capture))
        span.set_status(Status(StatusCode.OK))
        span.end()
        return reply
