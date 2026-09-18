"""Инструменты внешних серверов в наборе сервиса.

Инструмент внешнего сервера объявляется тем же типом, что и собственный: у цикла одно
описание инструмента, и внешний сервер есть лишь один из его источников. Поэтому усечение
объёмного результата, запись в журнал, трассировка и отказ инструмента как штатный исход
действуют на внешние инструменты сами собой.

Соединение здесь не открывается: оно открывается при первом вызове и живёт до конца хода.
"""

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

from ..core.external_tools import (
    PlannedExternalTool,
    plan_external_tools,
    server_instructions_section,
)
from ..core.tool import AgentTool, AnyAgentTool, ToolFailure, raw_input, tool_failure
from .client import McpCallFailure, McpTarget, McpTurnSession
from .directory import ActiveMcpConnection
from .result import CallFailed

_UNREACHABLE_HINT = (
    "Сервер MCP недоступен, и повтор того же вызова даст тот же результат. Если задача "
    "выполнима без этого инструмента — продолжай без него, иначе сообщи о недоступности."
)
_TOOL_REFUSAL_HINT = (
    "Это отказ самого инструмента, а не сервиса. Прочитай текст и исправь аргументы либо "
    "возьми другой инструмент."
)


@dataclass(frozen=True, slots=True)
class McpToolOptions:
    call_timeout_s: float
    on_diverged: Callable[[str, str], None]
    """Сообщение о том, что снимок разошёлся с сервером: идентификатор подключения и текст."""


def build_mcp_tools(
    connections: Sequence[ActiveMcpConnection], session: McpTurnSession, options: McpToolOptions
) -> list[AnyAgentTool]:
    tools: list[AnyAgentTool] = []
    for connection in connections:
        plan = plan_external_tools(connection.name, connection.snapshot.tools, connection.selection)
        target = McpTarget(connection.id, connection.name, connection.transport)
        for planned in plan.accepted:
            tools.append(
                AgentTool(
                    name=planned.name,
                    description=planned.description,
                    # Схема получена готовой JSON Schema; проверяет аргументы сам сервер.
                    input=raw_input(planned.input_schema),
                    execute=_executor(session, target, planned, options),
                    source="mcp",
                )
            )
    return tools


def mcp_prompt_sections(connections: Sequence[ActiveMcpConnection]) -> list[str]:
    """Секции системного промпта из текстовых инструкций серверов."""
    sections = []
    for connection in connections:
        section = server_instructions_section(connection.name, connection.snapshot)
        if section is not None:
            sections.append(section)
    return sections


def _executor(
    session: McpTurnSession,
    target: McpTarget,
    planned: PlannedExternalTool,
    options: McpToolOptions,
) -> Callable[[dict[str, Any]], Awaitable[object]]:
    async def execute(args: dict[str, Any]) -> object:
        try:
            outcome = await session.call(target, planned.remote_name, args, options.call_timeout_s)
        except McpCallFailure as failure:
            raise _to_tool_failure(failure, target, planned.name, options) from failure
        except Exception as error:
            raise tool_failure(
                f"Инструмент {planned.name} не выполнен", _UNREACHABLE_HINT, error
            ) from error
        if isinstance(outcome, CallFailed):
            raise ToolFailure(outcome.message, _TOOL_REFUSAL_HINT)
        return outcome.value

    return execute


def _to_tool_failure(
    failure: McpCallFailure, target: McpTarget, tool_name: str, options: McpToolOptions
) -> ToolFailure:
    """Переводит отказ обращения в отказ инструмента.

    Различие двух причин доходит до модели подсказкой. Недоступность сервера повторным
    вызовом не исправляется, и модель следует направить в обход. Отклонение вызова означает
    расхождение снимка с сервером: запись помечается, а модель извещается, что состав
    инструментов изменился.
    """
    if failure.kind == "rejected":
        options.on_diverged(target.id, failure.message)
        return ToolFailure(
            failure.message,
            f'Состав инструментов сервера "{target.name}" изменился с момента последнего '
            "обнаружения. Не повторяй тот же вызов: возьми другой инструмент либо сообщи, что "
            "инструмент стал недоступен.",
        )
    return ToolFailure(f"Инструмент {tool_name} не выполнен: {failure.message}", _UNREACHABLE_HINT)
