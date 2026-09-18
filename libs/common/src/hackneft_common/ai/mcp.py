"""Справочник подключений MCP.

Подключение — внешний сервер, поставляющий сервису инструменты. Сервис не содержит перечня
инструментов такого сервера: он хранит запись — где сервер находится и чем запускается, — а
состав узнаёт у самого сервера обнаружением и хранит снимком.
"""

from typing import Annotated, Literal

from pydantic import Field, JsonValue

from .base import ApiModel


class StdioTransport(ApiModel):
    """Сервер запускается дочерним процессом, обмен идёт через его stdin и stdout."""

    type: Literal["stdio"] = "stdio"
    command: str = Field(min_length=1, max_length=300)
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    cwd: str | None = Field(default=None, max_length=500)
    """Рабочий каталог процесса. Пусто означает каталог сервиса."""


class HttpTransport(ApiModel):
    """Сервер доступен по Streamable HTTP."""

    type: Literal["http"] = "http"
    url: str = Field(min_length=1, max_length=2000)
    headers: dict[str, str] = Field(default_factory=dict)


class SseTransport(ApiModel):
    """Транспорт, объявленный устаревшим в пользу Streamable HTTP.

    Принимается разбором, но не поддерживается: запись получает состояние `unsatisfied` с
    прямым сообщением. Отвергать вставленную конфигурацию целиком из-за одной записи хуже.
    """

    type: Literal["sse"] = "sse"
    url: str = Field(min_length=1, max_length=2000)
    headers: dict[str, str] = Field(default_factory=dict)


McpTransport = Annotated[StdioTransport | HttpTransport | SseTransport, Field(discriminator="type")]


class McpToolSnapshot(ApiModel):
    """Инструмент в снимке. Схема входа хранится готовой JSON Schema, полученной от сервера."""

    name: str
    """Имя на стороне сервера, без префикса подключения."""
    title: str | None = None
    description: str = ""
    input_schema: dict[str, JsonValue]


class McpSnapshot(ApiModel):
    """Снимок состава, полученный при обнаружении. Из него собирается набор инструментов хода."""

    discovered_at: str
    server_name: str | None
    server_version: str | None
    instructions: str | None
    """Текстовая инструкция сервера. Идёт отдельной секцией системного промпта."""
    tools: list[McpToolSnapshot]
    unused_capabilities: list[str] = Field(default_factory=list)
    """Разделы протокола, объявленные сервером и сервисом не используемые: ресурсы, промпты."""


McpCheckStatus = Literal["unknown", "ok", "unsatisfied", "unreachable"]
"""Состояние проверки подключения.

`unsatisfied` — подключение непригодно по причине, устранимой настройкой; `unreachable` —
внешняя сторона не отвечает. Исправляются они в разных местах, поэтому различаются.
"""

McpToolMode = Literal["all", "except", "selected"]
"""Режим отбора инструментов подключения.

`all` — используется весь состав снимка, новые инструменты сервера включаются сами. `except` —
весь состав, кроме исключённых. `selected` — только отобранные; новые инструменты не
включаются, пока их не отметят.
"""


class McpConnection(ApiModel):
    """Запись справочника подключений."""

    id: str
    name: str
    """Имя подключения. Оно же префикс имён инструментов."""
    title: str | None
    enabled: bool
    transport: McpTransport
    """Транспорт с вычищенными секретами: значения заголовков и переменных окружения заменены
    отметкой о наличии."""
    snapshot: McpSnapshot | None
    tool_mode: McpToolMode
    enabled_tools: list[str]
    excluded_tools: list[str]
    check_status: McpCheckStatus
    problems: list[str]
    """Что именно непригодно при состоянии `unsatisfied`."""
    last_check_at: str | None
    last_check_message: str | None
    stale: bool
    """Снимок разошёлся с сервером: вызов отклонён как неизвестный либо не соответствующий
    схеме. Снимается успешной проверкой."""
    created_at: str
    updated_at: str


MCP_NAME_PATTERN = r"^[a-z][a-z0-9_]{0,23}$"
"""Ограничение имени подключения. Оно же префикс имён инструментов."""


class CreateMcpConnectionRequest(ApiModel):
    name: str = Field(pattern=MCP_NAME_PATTERN)
    title: str | None = Field(default=None, max_length=200)
    transport: McpTransport


class UpdateMcpConnectionRequest(ApiModel):
    name: str | None = Field(default=None, pattern=MCP_NAME_PATTERN)
    title: str | None = Field(default=None, max_length=200)
    transport: McpTransport | None = None
    tool_mode: McpToolMode | None = None
    enabled_tools: list[str] | None = None
    excluded_tools: list[str] | None = None


class ToggleMcpConnectionRequest(ApiModel):
    enabled: bool


class ImportMcpConnectionsRequest(ApiModel):
    """Импорт конфигурации в сложившемся формате MCP-клиентов.

    Передаётся текстом: разбор выполняет сервис, и сообщение о неверном JSON должно называть
    место ошибки.
    """

    config_json: str = Field(alias="json", min_length=1, max_length=100_000)


class McpConnectionListResponse(ApiModel):
    connections: list[McpConnection]


class McpImportResponse(ApiModel):
    created: list[McpConnection]
    skipped: list[str]
    """Причины, по которым запись не добавлена, с указанием её ключа."""
