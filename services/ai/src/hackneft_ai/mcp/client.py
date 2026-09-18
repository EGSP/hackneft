"""Обращения к внешним серверам MCP.

Клиент знает два действия: обнаружить состав инструментов и вызвать инструмент. Хранением
записей он не занимается — этим занят справочник подключений.

Соединение живёт один ход агента и открывается лениво, при первом вызове инструмента данного
подключения. Ход, не обратившийся к внешним инструментам, не подключается вовсе: состав
инструментов берётся из снимка, и открывать соединение раньше первого вызова незачем.

Соединение открывается и закрывается в одной и той же задаче asyncio — задаче хода. Этого
требует SDK: его транспорты держат группы задач anyio, выйти из которых можно только в той
задаче, где в них вошли.
"""

import asyncio
import logging
import tempfile
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import IO, Any, Literal, TextIO, cast
from urllib.parse import urlparse

import httpx2
from mcp import Client, MCPError, StdioServerParameters, stdio_client
from mcp.client import Transport
from mcp.client.streamable_http import streamable_http_client
from mcp_types import (
    CONNECTION_CLOSED,
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    REQUEST_TIMEOUT,
    Implementation,
    Tool,
)

from hackneft_common.ai import (
    HttpTransport,
    McpSnapshot,
    McpToolSnapshot,
    McpTransport,
    SseTransport,
    StdioTransport,
)

from ..config import McpConfig
from ..core.tool import describe_cause
from ..db.schema import iso, utc_now
from .result import CallFailed, CallSucceeded, McpCallOutcome, normalize_call_result

logger = logging.getLogger(__name__)

_STDERR_TAIL_CHARS = 2000
"""Сколько символов вывода в stderr сохраняется для объяснения отказа."""
_MAX_LIST_PAGES = 50
_CLIENT_INFO = Implementation(name="hackneft-ai", version="0.1.0")


class McpFailure(Exception):
    """Отказ подключения.

    `unreachable` означает, что внешняя сторона не отвечает и исправление находится там;
    `unsatisfied` — что подключение непригодно по причине, устранимой настройкой.
    """

    def __init__(self, kind: Literal["unreachable", "unsatisfied"], message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message


class McpCallFailure(Exception):
    """Отказ вызова инструмента.

    `unreachable` означает недоступность сервера и не говорит ничего о снимке; `rejected` —
    что сервер ответил отказом на сам вызов, и снимок мог разойтись с действительным составом.
    """

    def __init__(self, kind: Literal["unreachable", "rejected"], message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message


@dataclass(frozen=True, slots=True)
class McpTarget:
    """Подключение в том виде, в каком оно нужно для обращения к серверу."""

    id: str
    name: str
    transport: McpTransport


def describe_transport(transport: McpTransport) -> str:
    """Краткое описание подключения для журнала и сообщений об отказе."""
    if isinstance(transport, StdioTransport):
        return " ".join((transport.command, *transport.args)).strip()
    return transport.url


class OpenedClient:
    def __init__(self, client: Client, stack: AsyncExitStack, errlog: IO[str] | None) -> None:
        self.client = client
        self._stack = stack
        self._errlog = errlog

    def stderr_tail(self) -> str:
        return _read_tail(self._errlog)

    async def close(self) -> None:
        await self._stack.aclose()


async def open_client(transport: McpTransport, timeout_s: float) -> OpenedClient:
    """Подключение и инициализация.

    Вывод дочернего процесса в stderr собирается с самого начала: у сервера, не сумевшего
    запуститься, это единственное объяснение причины.
    """
    stack = AsyncExitStack()
    errlog: TextIO | None = None
    try:
        wire: Transport
        if isinstance(transport, SseTransport):
            raise McpFailure(
                "unsatisfied",
                "Транспорт sse объявлен устаревшим в пользу Streamable HTTP и не поддерживается. "
                'Укажите "type": "http", если сервер его умеет.',
            )
        if isinstance(transport, HttpTransport):
            _check_url(transport.url)
            http = await stack.enter_async_context(
                httpx2.AsyncClient(
                    headers=transport.headers or None,
                    timeout=httpx2.Timeout(timeout_s, read=300),
                )
            )
            wire = streamable_http_client(transport.url, http_client=http)
        else:
            # Вывод процесса пишется во временный файл: транспорту нужен настоящий файловый
            # дескриптор, а буфер в памяти его не имеет.
            errlog = cast(
                TextIO,
                stack.enter_context(
                    tempfile.TemporaryFile("w+", encoding="utf-8", errors="replace")
                ),
            )
            wire = stdio_client(
                StdioServerParameters(
                    command=transport.command,
                    args=list(transport.args),
                    env=dict(transport.env) or None,
                    cwd=transport.cwd or None,
                ),
                errlog=errlog,
            )
        # Клиентские возможности не заявляются: sampling, elicitation и roots сервисом не
        # поддерживаются. Кеш ответов отключён: обнаружение должно видеть сервер, а не кеш.
        client = await stack.enter_async_context(
            Client(wire, read_timeout_seconds=timeout_s, client_info=_CLIENT_INFO, cache=None)
        )
    except Exception as error:
        tail = _read_tail(errlog)
        await stack.aclose()
        raise to_failure(error, transport, tail) from error
    except BaseException:
        await stack.aclose()
        raise
    return OpenedClient(client, stack, errlog)


async def list_all_tools(client: Client) -> list[Tool]:
    tools: list[Tool] = []
    cursor: str | None = None
    for _ in range(_MAX_LIST_PAGES):
        listed = await client.list_tools(cursor=cursor)
        tools.extend(listed.tools)
        cursor = listed.next_cursor
        if cursor is None:
            break
    return tools


def to_failure(error: BaseException, transport: McpTransport, output: str) -> McpFailure:
    """Различает отказы. Ошибка протокола с кодом, относящимся к самому запросу, означает,
    что сервер отвечает и отвергает запрос; всё остальное означает недоступность."""
    error = _unwrap(error)
    if isinstance(error, McpFailure):
        return error
    where = describe_transport(transport)
    detail = "" if output.strip() == "" else f"\n{output.strip()}"
    if isinstance(error, MCPError):
        if error.code == REQUEST_TIMEOUT:
            return McpFailure(
                "unreachable", f"Сервер MCP не ответил за отведённое время ({where}){detail}"
            )
        if error.code == CONNECTION_CLOSED:
            return McpFailure("unreachable", f"Соединение с сервером MCP закрыто ({where}){detail}")
        return McpFailure(
            "unsatisfied", f"Ошибка протокола MCP (код {error.code}): {error.message}{detail}"
        )
    return McpFailure(
        "unreachable", f"Сервер MCP недоступен ({where}): {describe_cause(error)}{detail}"
    )


class McpClient:
    def __init__(self, config: McpConfig) -> None:
        self._config = config

    async def discover(self, transport: McpTransport) -> McpSnapshot:
        """Обнаружение состава: подключение, инициализация, запрос перечня, закрытие.

        Проверка записи выполняется тем же способом, что и работа: второй механизм ради одной
        операции был бы избыточен.
        """
        opened = await open_client(transport, self._config.connect_timeout_s)
        try:
            tools = await list_all_tools(opened.client)
            info = opened.client.server_info
            capabilities = opened.client.server_capabilities
            unused = [
                label
                for label, present in (
                    ("ресурсы", capabilities.resources is not None),
                    ("промпты", capabilities.prompts is not None),
                )
                if present
            ]
            return McpSnapshot(
                discovered_at=iso(utc_now()),
                server_name=None if info is None else info.name,
                server_version=None if info is None else info.version,
                instructions=opened.client.instructions,
                tools=[_tool_snapshot(tool) for tool in tools],
                unused_capabilities=unused,
            )
        except Exception as error:
            raise to_failure(error, transport, opened.stderr_tail()) from error
        finally:
            await opened.close()

    def open_session(self) -> "McpTurnSession":
        """Набор соединений на один ход. Закрывается вызывающей стороной в задаче хода."""
        return McpTurnSession(self._config.connect_timeout_s)


class McpTurnSession:
    """Соединения одного хода.

    Каждое подключение открывается не более одного раза за ход. По завершении хода
    закрываются все открытые.
    """

    def __init__(self, connect_timeout_s: float) -> None:
        self._connect_timeout_s = connect_timeout_s
        self._clients: dict[str, OpenedClient] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._closed = False

    async def call(
        self, target: McpTarget, remote_name: str, args: dict[str, Any], timeout_s: float
    ) -> McpCallOutcome:
        """Вызывает инструмент внешнего сервера.

        Предел времени задаётся на вызов отдельно от предела на подключение: зависший сервер
        не должен удерживать ход до его отмены.
        """
        if self._closed:
            raise McpCallFailure("unreachable", "Ход завершён, соединение закрыто")
        try:
            opened = await self._client_for(target)
        except McpFailure as failure:
            raise McpCallFailure("unreachable", failure.message) from failure

        outcome: McpCallOutcome | None = None
        thrown: Exception | None = None
        try:
            raw = await opened.client.call_tool(remote_name, args, read_timeout_seconds=timeout_s)
            outcome = normalize_call_result(raw)
        except Exception as error:
            thrown = error

        if thrown is None and isinstance(outcome, CallSucceeded):
            return outcome

        # Неудачный вызов проверяется на расхождение снимка, и проверяется структурно, а не
        # по тексту ошибки: признак «инструмента нет» приходит от разных серверов по-разному.
        # Достоверный признак один — инструмента нет в перечне, который сервер объявляет
        # сейчас. Запрос перечня выполняется по уже открытому соединению и только на пути
        # отказа, который редок.
        if _server_answered(thrown) and await self._gone(opened, remote_name):
            detail = f": {outcome.message}" if isinstance(outcome, CallFailed) else ""
            raise McpCallFailure(
                "rejected",
                f'Сервер "{target.name}" больше не объявляет инструмент {remote_name}{detail}',
            )

        if thrown is not None:
            call_failure = self._to_call_failure(thrown, target, remote_name, opened.stderr_tail())
            if call_failure.kind == "unreachable":
                # Отказавшее соединение закрывается: следующий вызов за ход откроет новое,
                # сервер мог подняться между вызовами.
                await self._drop(target.id)
            raise call_failure from thrown
        assert outcome is not None
        return outcome

    async def close(self) -> None:
        """Закрывает все открытые за ход соединения. Отказ закрытия не должен ронять ход."""
        self._closed = True
        for target_id in list(self._clients):
            await self._drop(target_id)

    async def _client_for(self, target: McpTarget) -> OpenedClient:
        lock = self._locks.setdefault(target.id, asyncio.Lock())
        async with lock:
            existing = self._clients.get(target.id)
            if existing is not None:
                return existing
            opened = await open_client(target.transport, self._connect_timeout_s)
            self._clients[target.id] = opened
            return opened

    async def _drop(self, target_id: str) -> None:
        opened = self._clients.pop(target_id, None)
        if opened is None:
            return
        try:
            await opened.close()
        except Exception as error:
            logger.warning("закрытие соединения MCP не удалось: %s", describe_cause(error))

    async def _gone(self, opened: OpenedClient, remote_name: str) -> bool:
        """Отсутствует ли инструмент в том перечне, который сервер объявляет сейчас."""
        try:
            tools = await list_all_tools(opened.client)
        except Exception:
            # Перечень получить не удалось — утверждать расхождение не на чем.
            return False
        return all(tool.name != remote_name for tool in tools)

    @staticmethod
    def _to_call_failure(
        error: BaseException, target: McpTarget, remote_name: str, output: str
    ) -> McpCallFailure:
        error = _unwrap(error)
        if isinstance(error, MCPError) and error.code in (
            INVALID_PARAMS,
            METHOD_NOT_FOUND,
            INVALID_REQUEST,
        ):
            # Отказ, относящийся к самому вызову: инструмента нет либо аргументы не подошли.
            return McpCallFailure(
                "rejected",
                f'Сервер "{target.name}" отклонил вызов {remote_name}: {error.message}',
            )
        failure = to_failure(error, target.transport, output)
        return McpCallFailure(
            "rejected" if failure.kind == "unsatisfied" else "unreachable", failure.message
        )


def _server_answered(thrown: BaseException | None) -> bool:
    """Ответил ли сервер вообще. Истечение времени и закрытое соединение не говорят о составе
    инструментов ничего, и запрашивать перечень в этом случае значит ждать второй раз."""
    if thrown is None:
        return True
    error = _unwrap(thrown)
    if not isinstance(error, MCPError):
        return False
    return error.code not in (REQUEST_TIMEOUT, CONNECTION_CLOSED)


def _tool_snapshot(tool: Tool) -> McpToolSnapshot:
    title = tool.title or (tool.annotations.title if tool.annotations is not None else None)
    return McpToolSnapshot(
        name=tool.name,
        title=title,
        description=tool.description or "",
        input_schema=tool.input_schema,
    )


def _unwrap(error: BaseException) -> BaseException:
    """Первое исключение группы: группы задач anyio оборачивают отказы в ExceptionGroup."""
    while isinstance(error, BaseExceptionGroup) and error.exceptions:
        error = error.exceptions[0]
    return error


def _check_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or parsed.netloc == "":
        raise McpFailure(
            "unsatisfied", f'Адрес "{url}" не разобран как URL со схемой http или https'
        )


def _read_tail(errlog: IO[str] | None) -> str:
    if errlog is None or errlog.closed:
        return ""
    try:
        errlog.flush()
        errlog.seek(0)
        text = errlog.read()
        errlog.seek(0, 2)
    except (OSError, ValueError):
        return ""
    return text[-_STDERR_TAIL_CHARS:]
