"""Сборка инструментов внешнего сервера в набор сервиса.

Здесь только то, что не требует ни сети, ни базы данных: построение имени, проверка его
допустимости, отбор подмножества. Клиент протокола находится вне ядра — ядру о HTTP и
дочерних процессах знать незачем.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from hackneft_common.ai import McpSnapshot, McpToolMode, McpToolSnapshot

TOOL_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
"""Ограничение формата описания инструментов: до 64 символов из латинских букв, цифр,
подчёркивания и дефиса. Обрезка длинного имени порождает совпадения и молчаливую потерю
инструмента, поэтому имя отвергается целиком, а причина показывается в записи подключения."""


@dataclass(frozen=True, slots=True)
class McpToolSelection:
    """Отбор инструментов подключения: режим и оба перечня.

    Перечни хранят имена на стороне сервера и не очищаются при обнаружении. Инструмент,
    исчезнувший с сервера, остаётся в перечне, и если сервер вернёт его, запись уже знает,
    используется ли он.
    """

    mode: McpToolMode
    enabled: tuple[str, ...] = ()
    excluded: tuple[str, ...] = ()


ALL_TOOLS = McpToolSelection("all")
"""Отбор новой записи: весь состав, новые инструменты сервера включаются сами."""


def is_mcp_tool_used(selection: McpToolSelection, name: str) -> bool:
    if selection.mode == "all":
        return True
    if selection.mode == "except":
        return name not in selection.excluded
    return name in selection.enabled


def external_tool_name(prefix: str, remote_name: str) -> str:
    """Имя внешнего инструмента в пространстве имён сервиса."""
    return f"{prefix}_{remote_name}"


@dataclass(frozen=True, slots=True)
class PlannedExternalTool:
    name: str
    """Имя в пространстве имён сервиса: префикс подключения плюс имя на сервере."""
    remote_name: str
    """Имя на стороне сервера — с ним вызывается `tools/call`."""
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ExternalToolPlan:
    accepted: tuple[PlannedExternalTool, ...]
    rejected: tuple[str, ...]
    """Причины, по которым инструмент в набор не попал."""


def plan_external_tools(
    prefix: str, tools: Sequence[McpToolSnapshot], selection: McpToolSelection
) -> ExternalToolPlan:
    """Отбирает инструменты снимка, пригодные для набора.

    Перебирается снимок, а не перечень отбора: имя, оставшееся в перечне после исчезновения
    инструмента с сервера, в набор не попадает.
    """
    accepted: list[PlannedExternalTool] = []
    rejected: list[str] = []
    for tool in tools:
        if not is_mcp_tool_used(selection, tool.name):
            continue
        name = external_tool_name(prefix, tool.name)
        if TOOL_NAME_PATTERN.fullmatch(name) is None:
            rejected.append(
                f'{tool.name}: имя "{name}" не проходит проверку формата '
                "(до 64 символов из латинских букв, цифр, подчёркивания и дефиса)"
            )
            continue
        accepted.append(
            PlannedExternalTool(
                name=name,
                remote_name=tool.name,
                description=_describe_external_tool(tool),
                input_schema=dict(tool.input_schema),
            )
        )
    return ExternalToolPlan(tuple(accepted), tuple(rejected))


def _describe_external_tool(tool: McpToolSnapshot) -> str:
    """Текст, который увидит модель.

    Заголовок инструмента, если сервер его сообщил, ставится перед описанием: у части
    серверов описание пусто, и тогда заголовок остаётся единственным пояснением.
    """
    title = tool.title or None
    description = tool.description or None
    if title is not None and description is not None:
        return f"{title}. {description}"
    return title or description or "Инструмент внешнего сервера MCP без описания."


def server_instructions_section(name: str, snapshot: McpSnapshot) -> str | None:
    """Секция системного промпта из инструкции сервера.

    Описания самих инструментов сюда не дублируются: они уходят в поле `tools` запроса к
    модели. Инструкция же в поле `tools` не выражается вовсе — иначе передать её нечем.
    """
    instructions = snapshot.instructions
    if instructions is None or instructions.strip() == "":
        return None
    return f"## Сервер MCP «{snapshot.server_name or name}»\n\n{instructions.strip()}"
