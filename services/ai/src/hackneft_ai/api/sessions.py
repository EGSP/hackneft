"""API сессий."""

from collections.abc import AsyncIterator

from fastapi import APIRouter
from sse_starlette import EventSourceResponse, ServerSentEvent

from hackneft_common.ai import (
    AcceptedResponse,
    CreateSessionRequest,
    RenameSessionRequest,
    RequestSnapshot,
    SelectModelRequest,
    SendMessageRequest,
    Session,
    SessionContextResponse,
    SessionEvent,
    SessionEventsResponse,
    SessionKind,
    SessionListResponse,
    ToolListResponse,
)

from ..errors import NotFoundError
from .deps import ServicesDep, errors

router = APIRouter(prefix="/api/sessions", tags=["sessions"])

_HEARTBEAT_S = 25
"""Интервал служебных сообщений потока. Держится меньше таймаута простоя обратного прокси."""


@router.get("", summary="Перечень сессий")
async def list_sessions(
    services: ServicesDep, kind: SessionKind | None = None
) -> SessionListResponse:
    """Перечень корневых сессий, по времени последнего события."""
    return SessionListResponse(sessions=await services.sessions.list_sessions(kind))


@router.post("", summary="Создание сессии", responses=errors(400, 404, 409))
async def create_session(body: CreateSessionRequest, services: ServicesDep) -> Session:
    """Создание сессии. Агентская сессия начинает ход сразу; ответ содержит её идентификатор."""
    return await services.sessions.create(body)


# Маршруты без параметра пути объявлены до маршрутов с ним: иначе адрес был бы разобран как
# идентификатор сессии.


@router.get("/tools", summary="Состав инструментов")
async def list_tools(services: ServicesDep) -> ToolListResponse:
    """Полный состав инструментов: встроенные и полученные от серверов MCP."""
    return ToolListResponse(tools=await services.tools.describe_all())


@router.get("/snapshots/{snapshot_id}", summary="Снимок запроса", responses=errors(404))
async def get_snapshot(snapshot_id: str, services: ServicesDep) -> RequestSnapshot:
    """Снимок постоянной части запроса, на который ссылается событие начала шага."""
    snapshot = await services.snapshots.find(snapshot_id)
    if snapshot is None:
        raise NotFoundError(f"Снимок запроса {snapshot_id} не найден")
    return snapshot


@router.get("/{session_id}", summary="Сессия", responses=errors(404))
async def get_session(session_id: str, services: ServicesDep) -> Session:
    return await services.sessions.require(session_id)


@router.patch("/{session_id}", summary="Переименование сессии", responses=errors(404))
async def rename_session(
    session_id: str, body: RenameSessionRequest, services: ServicesDep
) -> Session:
    return await services.sessions.rename(session_id, body.title)


@router.delete("/{session_id}", summary="Удаление сессии", responses=errors(404))
async def remove_session(session_id: str, services: ServicesDep) -> AcceptedResponse:
    await services.sessions.remove(session_id)
    return AcceptedResponse(accepted=True)


@router.get(
    "/{session_id}/events",
    response_model_exclude_none=True,
    summary="События журнала",
    responses=errors(404),
)
async def read_events(
    session_id: str, services: ServicesDep, after: int = 0
) -> SessionEventsResponse:
    """События журнала с порядковым номером больше `after`."""
    await services.sessions.require(session_id)
    events = await services.journal.read(session_id, after)
    return SessionEventsResponse(events=events, last_seq=events[-1].seq if events else after)


@router.post("/{session_id}/messages", summary="Отправка сообщения", responses=errors(404, 409))
async def send_message(
    session_id: str, body: SendMessageRequest, services: ServicesDep
) -> AcceptedResponse:
    """Приём сообщения. Ответ возвращается сразу, а результат приходит событиями журнала.

    Если модели сессии нет в справочнике или провайдер её не предоставляет, сообщение
    записывается в журнал вместе с отказом хода `turn_failed` с причиной `model_missing` либо
    `model_unavailable`: пользователь видит отказ в диалоге.
    """
    await services.sessions.send(session_id, body.text)
    return AcceptedResponse(accepted=True)


@router.get("/{session_id}/context", summary="Заполненность контекста", responses=errors(404))
async def measure_context(session_id: str, services: ServicesDep) -> SessionContextResponse:
    """Состав контекста сессии. Вычисляется в момент запроса и нигде не хранится."""
    await services.sessions.require(session_id)
    return await services.runner.measure_context(session_id)


@router.post("/{session_id}/model", summary="Смена модели сессии", responses=errors(404, 409))
async def select_model(session_id: str, body: SelectModelRequest, services: ServicesDep) -> Session:
    """Смена модели сессии. Допустима только когда ход не идёт. Модель указывается любой
    ссылкой: идентификатором записи, идентификатором модели у провайдера либо синонимом."""
    return await services.sessions.select_model(session_id, body.reference)


@router.post("/{session_id}/interrupt", summary="Прерывание хода", responses=errors(404))
async def interrupt(session_id: str, services: ServicesDep) -> AcceptedResponse:
    """Прерывание сессии вместе с потомками."""
    return AcceptedResponse(accepted=await services.sessions.interrupt(session_id))


@router.get(
    "/{session_id}/stream",
    summary="Поток событий по SSE",
    response_class=EventSourceResponse,
    responses={
        200: {
            "description": (
                "Сообщения `event: event`: поле `id` — порядковый номер события, поле `data` — "
                "событие журнала в JSON, по той же схеме, что элементы `events` в ответе "
                "`/events`. Раз в 25 секунд идёт служебное сообщение `event: ping` с пустыми "
                "данными. При переподключении номер последнего полученного события передаётся "
                "параметром `after`."
            ),
            "content": {"text/event-stream": {"schema": {"type": "string"}}},
        },
        **errors(404),
    },
)
async def stream(session_id: str, services: ServicesDep, after: int = 0) -> EventSourceResponse:
    """Поток событий сессии по SSE.

    Сначала досылаются события, пропущенные подписчиком, затем идут новые. Подписка на новые
    события начинается до чтения пропущенных, иначе события, случившиеся во время догрузки,
    потерялись бы; возможный повтор отбрасывается по порядковому номеру.
    """
    await services.sessions.require(session_id)

    async def events() -> AsyncIterator[ServerSentEvent]:
        with services.bus.subscribe(session_id) as queue:
            last = after
            for event in await services.journal.read(session_id, after):
                last = event.seq
                yield _server_sent(event)
            while True:
                live = await queue.get()
                if live is None:
                    return
                if live.seq <= last:
                    continue
                last = live.seq
                yield _server_sent(live)

    return EventSourceResponse(
        events(),
        ping=_HEARTBEAT_S,
        ping_message_factory=lambda: ServerSentEvent(event="ping", data=""),
    )


def _server_sent(event: SessionEvent) -> ServerSentEvent:
    return ServerSentEvent(
        id=str(event.seq),
        event="event",
        data=event.model_dump_json(exclude_none=True),
    )
