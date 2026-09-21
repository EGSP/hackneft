"""Сессии.

Два вида сессии различаются тем, кто подаёт вход и что считается завершением; журнал, поток
событий и прерывание у них общие. Поэтому запись одна на оба вида, а расходится дисциплина
исполнения, которой распоряжается служба запуска ходов.

Сессии создаёт только этот сервис: идентификатор новой сессии вызывающая сторона получает в
ответе на запрос создания.
"""

from typing import get_args

from sqlalchemy import delete, select, update

from hackneft_common.ai import (
    ChildSessionStartedEvent,
    CreateSessionRequest,
    Session,
    SessionKind,
    SessionStatus,
)

from ..agents.service import AgentDirectory
from ..db.database import Database
from ..db.schema import SessionRow, iso
from ..errors import BadRequestError, ConflictError, NotFoundError
from ..models.service import ModelChoice, ModelDirectory, ModelTurnError
from .bus import SessionEventBus
from .journal import SessionJournal
from .runner import AgentRunner

_DEFAULT_CHAT_TITLE = "Новый чат"


class SessionsService:
    def __init__(
        self,
        db: Database,
        bus: SessionEventBus,
        journal: SessionJournal,
        models: ModelDirectory,
        agents: AgentDirectory,
        runner: AgentRunner,
    ) -> None:
        self._db = db
        self._bus = bus
        self._journal = journal
        self._models = models
        self._agents = agents
        self._runner = runner

    async def list_sessions(self, kind: SessionKind | None = None) -> list[Session]:
        """Перечень корневых сессий.

        Дочерние читаются из своего родителя по событию о порождении, и в общем списке они были
        бы шумом. Порядок — по времени последнего события журнала, а не по времени изменения
        записи: переименование и смена модели меняют запись, не будучи работой в сессии.
        """
        query = (
            select(SessionRow)
            .where(SessionRow.parent_id.is_(None))
            .order_by(SessionRow.last_event_at.desc(), SessionRow.created_at.desc())
        )
        if kind is not None:
            query = query.where(SessionRow.kind == kind)
        async with self._db.read() as session:
            rows = (await session.scalars(query)).all()
        return [_to_session(row) for row in rows]

    async def create(self, request: CreateSessionRequest) -> Session:
        """Создание сессии.

        Агентская сессия получает постановку задачи сразу и начинает ход немедленно. Она может
        быть корневой — её создаёт платформа — либо дочерней: тогда в журнал порождающей
        сессии записывается событие о порождении.
        """
        kind: SessionKind = request.kind or "chat"
        if kind == "agent" and request.task is None:
            raise BadRequestError("Для агентской сессии обязательна постановка задачи")
        if request.parent_id is not None:
            await self.require(request.parent_id)
        agent = None if request.agent is None else await self._agents.require(request.agent)

        # Модель выбирается до создания записи. Указанная явно должна существовать: подменять
        # её другой нельзя. Ссылка, в том числе синоним, разрешается здесь один раз, и дальше
        # сессия закреплена за записью. Иначе сессия получает модель по умолчанию. Агентская
        # сессия без модели не создаётся вовсе — исполнить задание ей нечем, — а чат при
        # пустом справочнике создаётся без модели и получает её первым ходом. Модель запроса
        # важнее модели карточки агента: вызывающая сторона выбрала её явно.
        model: ModelChoice | None
        ref = request.model or request.model_id or (None if agent is None else agent.model)
        if ref is not None:
            model = await self._models.resolve(ref)
        elif kind == "agent":
            model = await self._models.require_default()
        else:
            model = await self._models.default_model()

        title = (request.title or "").strip() or (
            _shorten(request.task or "Задача") if kind == "agent" else _DEFAULT_CHAT_TITLE
        )

        system_prompt = None if agent is None else await self._agents.session_prompt(agent)

        async with self._db.write() as tx:
            row = SessionRow(
                kind=kind,
                title=title,
                parent_id=request.parent_id,
                model_id=None if model is None else model.id,
                model_provider=None if model is None else model.provider,
                model_identifier=None if model is None else model.identifier,
                agent_id=None if agent is None else agent.id,
                system_prompt=system_prompt,
            )
            tx.add(row)
            await tx.flush()
            session_id = row.id

        if request.parent_id is not None:
            await self._journal.append(
                request.parent_id,
                ChildSessionStartedEvent(child_id=session_id, kind=kind, title=title),
            )

        if kind == "agent" and request.task is not None:
            self._runner.claim(session_id)
            try:
                await self._runner.submit_task(
                    session_id, request.task, request.tools, request.traceparent
                )
            except BaseException:
                self._runner.release(session_id)
                raise

        return await self.require(session_id)

    async def require(self, session_id: str) -> Session:
        return _to_session(await self._row(session_id))

    async def require_idle(self, session_id: str) -> Session:
        """Чат-сессия, в которой не идёт ход: только в этом состоянии она принимает сообщение и
        смену модели."""
        session = await self.require(session_id)
        if session.status == "running":
            raise ConflictError("В сессии уже выполняется ход")
        if session.kind != "chat":
            raise ConflictError(
                "Сообщения принимает только чат-сессия: агентская получает вход при создании"
            )
        return session

    async def send(self, session_id: str, text: str) -> None:
        """Приём сообщения. Ход запускается фоном и к времени жизни запроса не привязан.

        Модель определяется до запуска хода. Если модели сессии нет в справочнике или
        провайдер её не предоставляет, сообщение записывается вместе с отказом хода: так
        пользователь видит причину в диалоге. Прочие отказы журнал не меняют.
        """
        await self.require_idle(session_id)
        self._runner.claim(session_id)
        try:
            try:
                model = await self._runner.prepare(session_id)
            except ModelTurnError as error:
                await self._ensure_title(session_id, text)
                await self._runner.reject(session_id, text, error)
                return
            await self._ensure_title(session_id, text)
            await self._runner.submit(session_id, text, model)
        except BaseException:
            self._runner.release(session_id)
            raise

    async def interrupt(self, session_id: str) -> bool:
        """Прерывание сессии вместе с потомками.

        Обход начинается с потомков: прерванный родитель перестаёт ожидать их результата, и
        оставленные работать потомки расходовали бы токены впустую.
        """
        await self.require(session_id)
        return await self._interrupt_tree(session_id)

    async def _interrupt_tree(self, session_id: str) -> bool:
        async with self._db.read() as session:
            children = (
                await session.scalars(
                    select(SessionRow.id).where(SessionRow.parent_id == session_id)
                )
            ).all()
        interrupted = False
        for child_id in children:
            interrupted = await self._interrupt_tree(child_id) or interrupted
        return await self._runner.interrupt(session_id) or interrupted

    async def select_model(self, session_id: str, ref: str) -> Session:
        """Смена модели сессии по ссылке на модель.

        Разрешена только когда ход не идёт: иначе часть шагов была бы выполнена одной моделью,
        а часть другой. Наличие истории смене не мешает: массив сообщений собирается из журнала
        заново на каждый ход, поэтому новая модель получает весь прежний диалог как есть.
        """
        await self.require_idle(session_id)
        model = await self._models.resolve(ref)
        async with self._db.write() as tx:
            await tx.execute(
                update(SessionRow)
                .where(SessionRow.id == session_id)
                .values(
                    model_id=model.id,
                    model_provider=model.provider,
                    model_identifier=model.identifier,
                )
            )
        return await self.require(session_id)

    async def rename(self, session_id: str, title: str) -> Session:
        """Переименование. Разрешено в любом состоянии: название на исполнение не влияет."""
        await self.require(session_id)
        async with self._db.write() as tx:
            await tx.execute(
                update(SessionRow)
                .where(SessionRow.id == session_id)
                .values(title=" ".join(title.split()))
            )
        return await self.require(session_id)

    async def remove(self, session_id: str) -> None:
        """Удаление сессии вместе с потомками. Идущие в них ходы сначала прерываются."""
        await self.interrupt(session_id)
        self._bus.close(session_id)
        async with self._db.write() as tx:
            await tx.execute(delete(SessionRow).where(SessionRow.id == session_id))

    async def _ensure_title(self, session_id: str, first_message: str) -> None:
        """Название из первого сообщения. Отдельного обращения к модели за названием нет: это
        лишний запрос ради строки в списке."""
        row = await self._row(session_id)
        if row.event_count > 0 or row.title != _DEFAULT_CHAT_TITLE:
            return
        async with self._db.write() as tx:
            await tx.execute(
                update(SessionRow)
                .where(SessionRow.id == session_id)
                .values(title=_shorten(first_message))
            )

    async def _row(self, session_id: str) -> SessionRow:
        async with self._db.read() as session:
            row = await session.get(SessionRow, session_id)
        if row is None:
            raise NotFoundError(f"Сессия {session_id} не найдена")
        return row


def _shorten(text: str) -> str:
    return " ".join(text.split())[:60]


_KINDS: tuple[SessionKind, ...] = get_args(SessionKind)
_STATUSES: tuple[SessionStatus, ...] = get_args(SessionStatus)


def _to_session(row: SessionRow) -> Session:
    return Session(
        id=row.id,
        title=row.title,
        kind=next((kind for kind in _KINDS if kind == row.kind), "chat"),
        status=next((status for status in _STATUSES if status == row.status), "idle"),
        created_at=iso(row.created_at),
        updated_at=iso(row.updated_at),
        event_count=row.event_count,
        model_id=row.model_id,
        model_name=row.model_identifier,
        model_provider=row.model_provider,
        parent_id=row.parent_id,
        agent_id=row.agent_id,
        result=row.result,
        failure_message=row.failure_message,
    )
