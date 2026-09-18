"""Инструменты агента в том виде, в каком их видит клиент API."""

from typing import Literal

from .base import ApiModel

ToolSource = Literal["builtin", "mcp"]
"""Источник инструмента.

Встроенный написан внутри сервиса, внешний получен от сервера MCP. Признак нужен журналу,
интерфейсу и трассировке; на проверку аргументов он не влияет.
"""


class ToolInfo(ApiModel):
    """Описание инструмента: имя и текст, который видит модель."""

    name: str
    description: str
    source: ToolSource


class ToolListResponse(ApiModel):
    tools: list[ToolInfo]
