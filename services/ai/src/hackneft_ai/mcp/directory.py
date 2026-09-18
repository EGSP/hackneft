"""Справочник подключений MCP.

Запись хранит, где сервер находится и чем запускается; состав инструментов сервис узнаёт у
самого сервера обнаружением и хранит снимком. Снимок служит источником истины: набор
инструментов хода собирается из него, без обращения к серверу, поэтому задержка начала хода
не зависит от числа подключений.

Имена встроенных инструментов справочнику не нужны: он поставляет инструменты, а сверяет имена
тот, кто собирает набор целиком.
"""

import asyncio
import logging
from dataclasses import dataclass

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import select, update

from hackneft_common.ai import (
    CreateMcpConnectionRequest,
    HttpTransport,
    McpCheckStatus,
    McpConnection,
    McpSnapshot,
    McpToolMode,
    McpTransport,
    SseTransport,
    StdioTransport,
    UpdateMcpConnectionRequest,
)

from ..core.external_tools import ALL_TOOLS, McpToolSelection, plan_external_tools
from ..db.database import Database
from ..db.schema import McpConnectionRow, iso, utc_now
from ..errors import BadRequestError, ConflictError, NotFoundError
from .client import McpClient, McpFailure
from .config_file import McpConfigError, parse_mcp_servers_file

logger = logging.getLogger(__name__)

_TRANSPORT_ADAPTER: TypeAdapter[McpTransport] = TypeAdapter(McpTransport)
_MASK = "••••••"


@dataclass(frozen=True, slots=True)
class ActiveMcpConnection:
    """Подключение в том виде, в каком оно нужно сборщику набора инструментов."""

    id: str
    name: str
    transport: McpTransport
    snapshot: McpSnapshot
    selection: McpToolSelection


@dataclass(frozen=True, slots=True)
class _Discovery:
    snapshot: McpSnapshot | None
    status: McpCheckStatus
    problems: list[str]
    message: str


class McpDirectory:
    def __init__(self, db: Database, client: McpClient) -> None:
        self._db = db
        self._client = client
        self._rechecks: dict[str, asyncio.Task[None]] = {}
        """Повторные обнаружения, начатые после расхождения, — по одному на подключение."""
        self._background: set[asyncio.Task[None]] = set()

    async def list_connections(self) -> list[McpConnection]:
        async with self._db.read() as session:
            rows = (
                await session.scalars(select(McpConnectionRow).order_by(McpConnectionRow.name))
            ).all()
        return [_to_connection(row) for row in rows]

    async def require(self, connection_id: str) -> McpConnection:
        return _to_connection(await self._row(connection_id))

    async def active(self) -> list[ActiveMcpConnection]:
        """Подключения, пригодные для набора инструментов: включённые, прошедшие проверку и
        получившие снимок. Транспорт возвращается без вычищения секретов — он нужен для
        обращения к серверу, а не для показа.

        Снимок подключения с отметкой о расхождении известен как неверный, поэтому набор из
        него не собирается: сначала дожидается повторное обнаружение.
        """
        rows = await self._active_rows()
        stale = [row.id for row in rows if row.stale]
        if stale:
            await asyncio.gather(*(self._recheck(connection_id) for connection_id in stale))
            rows = await self._active_rows()

        active = []
        for row in rows:
            snapshot = _parse_snapshot(row.snapshot)
            transport = _parse_transport(row.config)
            if snapshot is None or transport is None:
                logger.warning("подключение «%s»: снимок или конфигурация не разобраны", row.name)
                continue
            active.append(
                ActiveMcpConnection(row.id, row.name, transport, snapshot, _selection(row))
            )
        return active

    async def create(self, request: CreateMcpConnectionRequest) -> McpConnection:
        """Создание записи.

        Обнаружение выполняется до записи: подключение, ни разу не подтвердившее
        работоспособность, создаёт ложное представление о доступных возможностях. Поэтому
        недостижимый сервер добавить нельзя. Сервер, который ответил, но непригоден, —
        добавляется с состоянием `unsatisfied`: причина видна в записи и исправляется
        настройкой.
        """
        await self._ensure_name_free(request.name)
        outcome = await self._discover(request.name, request.transport, ALL_TOOLS)
        if outcome.status == "unreachable":
            raise BadRequestError(f"Подключение не создано: {outcome.message}")

        async with self._db.write() as tx:
            row = McpConnectionRow(
                name=request.name,
                title=request.title,
                transport=request.transport.type,
                config=request.transport.model_dump(mode="json"),
                snapshot=None
                if outcome.snapshot is None
                else outcome.snapshot.model_dump(mode="json"),
                tool_mode=ALL_TOOLS.mode,
                enabled_tools=[],
                excluded_tools=[],
                check_status=outcome.status,
                problems=outcome.problems,
                stale=False,
                last_check_at=utc_now(),
                last_check_message=outcome.message,
            )
            tx.add(row)
            await tx.flush()
            return _to_connection(row)

    async def import_config(self, text: str) -> tuple[list[McpConnection], list[str]]:
        """Импорт конфигурации в сложившемся формате MCP-клиентов.

        Записи обрабатываются по отдельности и независимо: одна недостижимая не должна
        отменять остальные. Итог сообщается перечнем добавленного и перечнем причин, по
        которым остальное не добавлено.
        """
        try:
            entries = parse_mcp_servers_file(text)
        except McpConfigError as error:
            raise BadRequestError(str(error)) from error

        created: list[McpConnection] = []
        skipped: list[str] = []
        for entry in entries:
            try:
                created.append(
                    await self.create(
                        CreateMcpConnectionRequest(
                            name=entry.name,
                            title=None if entry.source_key == entry.name else entry.source_key,
                            transport=entry.transport,
                        )
                    )
                )
            except (ConflictError, BadRequestError) as error:
                skipped.append(f"{entry.source_key}: {error.message}")
            except ValidationError as error:
                skipped.append(f"{entry.source_key}: {error}")

        if not created:
            raise BadRequestError("Ни одно подключение не добавлено.\n" + "\n".join(skipped))
        return created, skipped

    async def update(
        self, connection_id: str, request: UpdateMcpConnectionRequest
    ) -> McpConnection:
        """Правка записи. Изменение транспорта обесценивает прежний снимок и влечёт повторное
        обнаружение; правка названия и отбора инструментов — не влечёт."""
        row = await self._row(connection_id)
        if request.name is not None and request.name != row.name:
            await self._ensure_name_free(request.name)

        name = request.name or row.name
        previous = _parse_transport(row.config)
        requested = (
            None if request.transport is None else _unmask_transport(request.transport, previous)
        )
        transport_changed = requested is not None and (
            previous is None or requested.model_dump() != previous.model_dump()
        )
        transport = requested or previous

        # Отбор инструментов участвует в проверке имён, поэтому берётся новый, если он задан.
        # Перечни заменяются только целиком и только явно: обнаружение их не трогает.
        current = _selection(row)
        selection = McpToolSelection(
            mode=request.tool_mode or current.mode,
            enabled=current.enabled
            if request.enabled_tools is None
            else tuple(request.enabled_tools),
            excluded=current.excluded
            if request.excluded_tools is None
            else tuple(request.excluded_tools),
        )

        values: dict[str, object] = {
            "name": name,
            "tool_mode": selection.mode,
            "enabled_tools": list(selection.enabled),
            "excluded_tools": list(selection.excluded),
        }
        if "title" in request.model_fields_set:
            values["title"] = request.title
        if requested is not None:
            values["transport"] = requested.type
            values["config"] = requested.model_dump(mode="json")
        async with self._db.write() as tx:
            await tx.execute(
                update(McpConnectionRow)
                .where(McpConnectionRow.id == connection_id)
                .values(**values)
            )

        if transport is None:
            raise BadRequestError("Конфигурация подключения не разобрана; задайте её заново")
        # Переименование меняет префикс, а с ним и имена инструментов; отбор меняет их состав.
        # И то и другое требует повторной проверки имён, но не обращения к серверу.
        if transport_changed:
            return await self.check(connection_id)
        return await self._revalidate(connection_id, name, selection)

    async def remove(self, connection_id: str) -> None:
        async with self._db.write() as tx:
            row = await tx.get(McpConnectionRow, connection_id)
            if row is None:
                raise NotFoundError(f"Подключение {connection_id} не найдено")
            await tx.delete(row)

    async def toggle(self, connection_id: str, enabled: bool) -> McpConnection:
        """Включение и выключение. Выключение выполняется без обращения к серверу, включение —
        с обнаружением: включать подключение, о работоспособности которого ничего не
        известно, значит обещать агенту инструменты, которых может не быть."""
        await self._row(connection_id)
        if enabled:
            checked = await self.check(connection_id)
            if checked.check_status == "unreachable":
                raise BadRequestError(
                    "Не удалось включить подключение: "
                    f"{checked.last_check_message or 'сервер недоступен'}"
                )
        async with self._db.write() as tx:
            await tx.execute(
                update(McpConnectionRow)
                .where(McpConnectionRow.id == connection_id)
                .values(enabled=enabled)
            )
        return await self.require(connection_id)

    async def check(self, connection_id: str) -> McpConnection:
        """Повторное обнаружение: подключение к серверу и запрос перечня инструментов."""
        row = await self._row(connection_id)
        transport = _parse_transport(row.config)
        if transport is None:
            return await self._record(
                connection_id,
                _Discovery(
                    None,
                    "unsatisfied",
                    ["конфигурация подключения не разобрана"],
                    "Конфигурация подключения не разобрана; задайте её заново.",
                ),
            )
        outcome = await self._discover(row.name, transport, _selection(row))
        return await self._record(connection_id, outcome)

    def schedule_stale(self, connection_id: str, message: str) -> None:
        """Отметка о расхождении в фоне: ход, заметивший расхождение, её не ждёт."""
        task = asyncio.create_task(self.mark_stale(connection_id, message))
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def mark_stale(self, connection_id: str, message: str) -> None:
        """Отметка о расхождении снимка с сервером.

        Ставится, когда сервер отклонил вызов как неизвестный либо не соответствующий схеме.
        Отметка влечёт повторное обнаружение в фоне: без него следующий ход собрался бы из
        того же снимка и снова предложил модели отсутствующий инструмент. Отметка
        записывается до начала проверки, поэтому переживает перезапуск процесса.
        """
        try:
            async with self._db.write() as tx:
                await tx.execute(
                    update(McpConnectionRow)
                    .where(McpConnectionRow.id == connection_id)
                    .values(stale=True, last_check_message=message)
                )
        except Exception as error:
            logger.warning(
                "отметка о расхождении подключения %s не записана: %s", connection_id, error
            )
            return
        self._recheck(connection_id)

    def _recheck(self, connection_id: str) -> asyncio.Task[None]:
        """Повторное обнаружение после расхождения. Одновременные расхождения по одному
        подключению сводятся к одной проверке."""
        pending = self._rechecks.get(connection_id)
        if pending is not None:
            return pending

        async def run() -> None:
            try:
                await self.check(connection_id)
            except Exception as error:
                logger.warning(
                    "повторное обнаружение подключения %s после расхождения не удалось: %s",
                    connection_id,
                    error,
                )
            finally:
                self._rechecks.pop(connection_id, None)

        task = asyncio.create_task(run(), name=f"mcp-recheck-{connection_id}")
        self._rechecks[connection_id] = task
        return task

    async def _revalidate(
        self, connection_id: str, name: str, selection: McpToolSelection
    ) -> McpConnection:
        """Проверка имён без обращения к серверу: снимок остаётся прежним."""
        row = await self._row(connection_id)
        snapshot = _parse_snapshot(row.snapshot)
        if snapshot is None:
            return await self.check(connection_id)
        problems = _describe_problems(name, snapshot, selection)
        if problems:
            outcome = _Discovery(snapshot, "unsatisfied", problems, _unsuitable(problems))
        else:
            accepted = (
                f"Состав из {len(snapshot.tools)} инструментов принят без обращения к серверу."
            )
            outcome = _Discovery(snapshot, "ok", [], accepted)
        return await self._record(connection_id, outcome)

    async def _discover(
        self, name: str, transport: McpTransport, selection: McpToolSelection
    ) -> _Discovery:
        """Обращение к серверу и оценка полученного."""
        try:
            snapshot = await self._client.discover(transport)
        except McpFailure as failure:
            logger.warning("обнаружение подключения «%s» не удалось: %s", name, failure.message)
            return _Discovery(None, failure.kind, [failure.message], failure.message)

        problems = _describe_problems(name, snapshot, selection)
        if problems:
            return _Discovery(snapshot, "unsatisfied", problems, _unsuitable(problems))
        server = f"{snapshot.server_name or 'без имени'} {snapshot.server_version or ''}".strip()
        return _Discovery(
            snapshot, "ok", [], f"Получено инструментов: {len(snapshot.tools)}. Сервер {server}"
        )

    async def _record(self, connection_id: str, outcome: _Discovery) -> McpConnection:
        values: dict[str, object] = {
            "check_status": outcome.status,
            "problems": outcome.problems,
            # Проверка снимает отметку о расхождении: состав получен заново.
            "stale": False,
            "last_check_at": utc_now(),
            "last_check_message": outcome.message,
        }
        if outcome.snapshot is not None:
            values["snapshot"] = outcome.snapshot.model_dump(mode="json")
        async with self._db.write() as tx:
            await tx.execute(
                update(McpConnectionRow)
                .where(McpConnectionRow.id == connection_id)
                .values(**values)
            )
        return await self.require(connection_id)

    async def _active_rows(self) -> list[McpConnectionRow]:
        async with self._db.read() as session:
            rows = await session.scalars(
                select(McpConnectionRow)
                .where(McpConnectionRow.enabled, McpConnectionRow.check_status == "ok")
                .order_by(McpConnectionRow.name)
            )
            return list(rows)

    async def _ensure_name_free(self, name: str) -> None:
        async with self._db.read() as session:
            existing = await session.scalar(
                select(McpConnectionRow.id).where(McpConnectionRow.name == name)
            )
        if existing is not None:
            raise ConflictError(f'Подключение "{name}" уже добавлено')

    async def _row(self, connection_id: str) -> McpConnectionRow:
        async with self._db.read() as session:
            row = await session.get(McpConnectionRow, connection_id)
        if row is None:
            raise NotFoundError(f"Подключение {connection_id} не найдено")
        return row


def _unsuitable(problems: list[str]) -> str:
    return f"Непригодно: {'; '.join(problems)}."


def _describe_problems(name: str, snapshot: McpSnapshot, selection: McpToolSelection) -> list[str]:
    """Что делает подключение непригодным.

    Имена из перечня отбора, которых нет на сервере, непригодности не означают: перечень
    хранит их намеренно, а в набор они не попадают. Непригодно подключение, которому нечего
    передать агенту, и причина называется по режиму, поскольку исправляется по-разному.
    """
    if not snapshot.tools:
        return ["сервер не объявил ни одного инструмента"]
    plan = plan_external_tools(name, snapshot.tools, selection)
    if plan.accepted:
        return list(plan.rejected)
    if plan.rejected:
        emptiness = "после отбора не осталось ни одного пригодного инструмента"
    elif selection.mode == "except":
        emptiness = "исключены все инструменты сервера"
    elif not selection.enabled:
        emptiness = "не отобран ни один инструмент"
    else:
        emptiness = "ни одного из отобранных инструментов нет на сервере"
    return [*plan.rejected, emptiness]


_TOOL_MODES: dict[str, McpToolMode] = {"all": "all", "except": "except", "selected": "selected"}
_CHECK_STATUSES: dict[str, McpCheckStatus] = {
    "ok": "ok",
    "unsatisfied": "unsatisfied",
    "unreachable": "unreachable",
}


def _selection(row: McpConnectionRow) -> McpToolSelection:
    return McpToolSelection(
        mode=_TOOL_MODES.get(row.tool_mode, "all"),
        enabled=tuple(_strings(row.enabled_tools)),
        excluded=tuple(_strings(row.excluded_tools)),
    )


def _strings(value: object) -> list[str]:
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def _parse_snapshot(value: object) -> McpSnapshot | None:
    if value is None:
        return None
    try:
        return McpSnapshot.model_validate(value)
    except ValidationError:
        return None


def _parse_transport(value: object) -> McpTransport | None:
    try:
        return _TRANSPORT_ADAPTER.validate_python(value)
    except ValidationError:
        return None


def _mask_transport(value: object) -> McpTransport:
    """Вычищает секреты из транспорта перед выдачей наружу.

    Возвращать вставленный токен в ответе API не следует и без шифрования конфигурации: он
    оказался бы в журнале клиента при первом же открытии записи.
    """
    transport = _parse_transport(value)
    if transport is None:
        return StdioTransport(command="(конфигурация не разобрана)")
    if isinstance(transport, StdioTransport):
        return transport.model_copy(update={"env": dict.fromkeys(transport.env, _MASK)})
    if isinstance(transport, HttpTransport | SseTransport):
        return transport.model_copy(update={"headers": dict.fromkeys(transport.headers, _MASK)})
    return transport


def _unmask_transport(transport: McpTransport, previous: McpTransport | None) -> McpTransport:
    """Заменяет маску прежними значениями, как при правке карточки провайдера.

    Запись приходит клиенту с замаскированными переменными окружения и заголовками. Без этой
    замены клиент, получивший запись и отправивший её обратно, записал бы маску вместо
    секретов. Маска у ключа, которого прежде не было, сохраняется как значение.
    """
    if isinstance(transport, StdioTransport):
        kept = previous.env if isinstance(previous, StdioTransport) else {}
        env = {k: kept.get(k, v) if v == _MASK else v for k, v in transport.env.items()}
        return transport.model_copy(update={"env": env})
    kept = previous.headers if isinstance(previous, HttpTransport | SseTransport) else {}
    headers = {k: kept.get(k, v) if v == _MASK else v for k, v in transport.headers.items()}
    return transport.model_copy(update={"headers": headers})


def _to_connection(row: McpConnectionRow) -> McpConnection:
    selection = _selection(row)
    return McpConnection(
        id=row.id,
        name=row.name,
        title=row.title,
        enabled=row.enabled,
        transport=_mask_transport(row.config),
        snapshot=_parse_snapshot(row.snapshot),
        tool_mode=selection.mode,
        enabled_tools=list(selection.enabled),
        excluded_tools=list(selection.excluded),
        check_status=_CHECK_STATUSES.get(row.check_status, "unknown"),
        problems=_strings(row.problems),
        last_check_at=None if row.last_check_at is None else iso(row.last_check_at),
        last_check_message=row.last_check_message,
        stale=row.stale,
        created_at=iso(row.created_at),
        updated_at=iso(row.updated_at),
    )
