"""Разбор конфигурации серверов в сложившемся формате MCP-клиентов.

Описание чужого формата со всеми его послаблениями отделено от нормализованного
представления сервиса намеренно: смешение привело бы к тому, что послабления чужого формата
попали бы в базу данных.
"""

import json
import re
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, ValidationError

from hackneft_common.ai import HttpTransport, McpTransport, SseTransport, StdioTransport

from ..core.tool import describe_validation_error


class McpConfigError(Exception):
    pass


class _ServerEntry(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: str | None = None
    command: str | None = None
    args: list[str] | None = None
    env: dict[str, str] | None = None
    cwd: str | None = None
    url: str | None = None
    headers: dict[str, str] | None = None


@dataclass(frozen=True, slots=True)
class McpImportEntry:
    name: str
    """Имя из ключа конфигурации, приведённое к допустимому имени подключения."""
    source_key: str
    """Исходный ключ — на случай, если приведение его изменило."""
    transport: McpTransport


def to_connection_name(key: str) -> str:
    """Приводит ключ конфигурации к имени подключения.

    Ключи в чужих конфигурациях пишутся через дефис и в верхнем регистре, тогда как имя
    подключения служит префиксом имени инструмента и ограничено строже.
    """
    slug = re.sub(r"[^a-z0-9_]+", "_", key.lower())
    slug = re.sub(r"_+", "_", slug).strip("_")[:24].rstrip("_")
    if re.match(r"^[a-z]", slug):
        return slug
    return f"mcp_{slug}"[:24].rstrip("_")


def parse_mcp_servers_file(text: str) -> list[McpImportEntry]:
    """Разбирает конфигурацию: `{"mcpServers": {...}}`, `{"servers": {...}}` либо перечень
    серверов без обёртки.

    Транспорт определяется по составу записи, если он не указан явно: `command` означает
    `stdio`, `url` — `http`. Одновременное присутствие обоих есть ошибка: угадывание намерения
    привело бы к подключению не к тому серверу.
    """
    try:
        raw: object = json.loads(text)
    except json.JSONDecodeError as error:
        raise McpConfigError(f"JSON не разобран: {error}") from error

    if not isinstance(raw, dict):
        raise McpConfigError(
            'Ожидается объект вида {"mcpServers": {"имя": {…}}} либо перечень серверов без обёртки.'
        )
    servers = raw.get("mcpServers", raw.get("servers", raw))
    if not isinstance(servers, dict):
        raise McpConfigError("Перечень серверов должен быть объектом: имя сервера — его описание.")
    if not servers:
        raise McpConfigError("В конфигурации нет ни одного сервера")

    entries = []
    for key, value in servers.items():
        try:
            entry = _ServerEntry.model_validate(value)
        except ValidationError as error:
            raise McpConfigError(
                f'Сервер "{key}": запись не разобрана: {describe_validation_error(error)}'
            ) from error
        entries.append(
            McpImportEntry(
                name=to_connection_name(key), source_key=key, transport=_transport(key, entry)
            )
        )
    return entries


def _transport(key: str, entry: _ServerEntry) -> McpTransport:
    has_command = bool(entry.command)
    has_url = bool(entry.url)
    if has_command and has_url:
        raise McpConfigError(
            f'Сервер "{key}": заданы одновременно command и url. Оставьте одно — по нему '
            "определяется транспорт."
        )

    declared = "http" if entry.type == "streamable-http" else entry.type
    kind = declared or ("stdio" if has_command else "http" if has_url else None)
    if kind is None:
        raise McpConfigError(f'Сервер "{key}": не заданы ни command, ни url, и тип не указан явно.')

    if kind == "stdio":
        if entry.command is None or entry.command == "":
            raise McpConfigError(f'Сервер "{key}": для транспорта stdio нужна command.')
        return StdioTransport(
            command=entry.command, args=entry.args or [], env=entry.env or {}, cwd=entry.cwd
        )

    if entry.url is None or entry.url == "":
        raise McpConfigError(f'Сервер "{key}": для транспорта {kind} нужен url.')
    if kind == "sse":
        return SseTransport(url=entry.url, headers=entry.headers or {})
    if kind != "http":
        raise McpConfigError(f'Сервер "{key}": неизвестный тип транспорта "{kind}".')
    return HttpTransport(url=entry.url, headers=entry.headers or {})
