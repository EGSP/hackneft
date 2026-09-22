"""Сессии ИИ-сервиса для страницы «Сессии» веб-интерфейса.

Страница служит прослеживаемости агентов: по журналу сессии видны постановка задачи, шаги,
обращения к модели, вызовы инструментов с аргументами и результатами, порождённые дочерние
сессии и итог. Сессии создаёт и хранит ИИ-сервис; платформа только передаёт ему запросы
страницы и возвращает ответы.

Запросы идут через платформу, а не из браузера напрямую, по двум причинам. Интерфейс отдаёт
тот же FastAPI, что и API, и обращение браузера к другому источнику потребовало бы настройки
CORS. Кроме того, внутри сети compose ИИ-сервис доступен по имени `http://ai:8100`, которое
браузеру неизвестно.

Передаются только запросы чтения: страница сессиями не управляет. Тела ответов не
разбираются и уходят браузеру как есть — поля в camelCase, по схемам `hackneft_common.ai`, —
поэтому новое поле журнала доходит до интерфейса без правки платформы. Схемы указаны в
описании эндпоинтов только для документации OpenAPI.
"""

from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import quote

import anyio
import httpx
from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from hackneft_common.ai import (
    RequestSnapshot,
    Session,
    SessionContextResponse,
    SessionEventsResponse,
    SessionKind,
    SessionListResponse,
    ToolListResponse,
)
from hackneft_platform.config import ai_service_url

router = APIRouter(prefix="/api/ai/sessions", tags=["ai-sessions"])

_TIMEOUT = httpx.Timeout(15.0)
"""Предел ожидания ответа на обычный запрос. Самый долгий из них — оценка контекста: сервис
пересчитывает токены всей переписки при каждом обращении."""

_STREAM_TIMEOUT = httpx.Timeout(15.0, read=None)
"""Поток событий открыт всё время, пока страница показывает сессию, и данные в нём идут с
перерывами: пока модель формирует ответ, ИИ-сервис шлёт лишь служебное сообщение раз в
25 секунд. Поэтому время чтения потока не ограничено, ограничено только установление
соединения."""


def _ok(model: type[BaseModel]) -> dict[int | str, dict[str, Any]]:
    """Описание успешного ответа для схемы OpenAPI: тело возвращается без разбора, и вывести
    схему из сигнатуры обработчика FastAPI не может."""
    return {200: {"model": model, "description": "Ответ ИИ-сервиса без изменений"}}


def _session_path(session_id: str, suffix: str = "") -> str:
    # Идентификатор кодируется целиком: он приходит из адреса страницы, и косая черта в нём
    # изменила бы путь запроса к ИИ-сервису.
    return f"/api/sessions/{quote(session_id, safe='')}{suffix}"


def _unavailable(error: httpx.HTTPError) -> HTTPException:
    """Отказ соединения с ИИ-сервисом.

    Код 502 отличает его от отказов самого ИИ-сервиса — например, «сессия не найдена», —
    которые передаются странице с исходным кодом и телом.
    """
    reason = str(error) or type(error).__name__
    return HTTPException(
        status_code=502, detail=f"ИИ-сервис {ai_service_url()} недоступен: {reason}"
    )


async def _forward(path: str, params: dict[str, Any] | None = None) -> Response:
    """Запрос чтения к ИИ-сервису. Ответ возвращается с тем же кодом состояния и телом."""
    try:
        async with httpx.AsyncClient(base_url=ai_service_url(), timeout=_TIMEOUT) as client:
            upstream = await client.get(path, params=params)
    except httpx.HTTPError as error:
        raise _unavailable(error) from error
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type"),
    )


@router.get("", summary="Перечень сессий", responses=_ok(SessionListResponse))
async def list_sessions(kind: SessionKind | None = None) -> Response:
    """Корневые сессии по времени последнего события. Дочерние в перечень не входят: они
    читаются по событию о порождении в журнале родителя."""
    return await _forward("/api/sessions", None if kind is None else {"kind": kind})


# Маршруты с постоянным началом пути объявлены до маршрутов с идентификатором сессии: иначе
# адрес был бы разобран как идентификатор сессии.


@router.get("/tools", summary="Состав инструментов", responses=_ok(ToolListResponse))
async def list_tools() -> Response:
    """Инструменты, которые ИИ-сервис предоставляет агентам: встроенные и полученные от
    серверов MCP, в том числе от MCP-сервера платформы."""
    return await _forward("/api/sessions/tools")


@router.get("/snapshots/{snapshot_id}", summary="Снимок запроса", responses=_ok(RequestSnapshot))
async def get_snapshot(snapshot_id: str) -> Response:
    """Постоянная часть запроса к модели, на которую ссылается событие начала шага: указания
    сервиса, секции серверов MCP и описания инструментов в том виде, в каком их видела
    модель."""
    return await _forward(f"/api/sessions/snapshots/{quote(snapshot_id, safe='')}")


@router.get("/{session_id}", summary="Сессия", responses=_ok(Session))
async def get_session(session_id: str) -> Response:
    return await _forward(_session_path(session_id))


@router.get("/{session_id}/events", summary="События журнала", responses=_ok(SessionEventsResponse))
async def read_events(session_id: str, after: int = 0) -> Response:
    """События журнала с порядковым номером больше `after`."""
    return await _forward(_session_path(session_id, "/events"), {"after": after})


@router.get(
    "/{session_id}/context",
    summary="Заполненность контекста",
    responses=_ok(SessionContextResponse),
)
async def measure_context(session_id: str) -> Response:
    """Оценка состава контекста сессии. ИИ-сервис вычисляет её в момент запроса."""
    return await _forward(_session_path(session_id, "/context"))


@router.get(
    "/{session_id}/stream",
    summary="Поток событий по SSE",
    response_class=StreamingResponse,
    responses={
        200: {
            "description": (
                "Поток ИИ-сервиса без изменений: сообщения `event: event` с событием журнала "
                "в поле `data` и служебные `event: ping`."
            ),
            "content": {"text/event-stream": {"schema": {"type": "string"}}},
        }
    },
)
async def stream_events(session_id: str, after: int = 0) -> Response:
    """Поток событий сессии: сначала пропущенные после `after`, затем новые.

    Соединение с ИИ-сервисом держится, пока открыт поток к браузеру. Когда браузер
    закрывает соединение, Starlette отменяет передачу, и соединение с ИИ-сервисом
    закрывается следом: иначе ИИ-сервис продолжал бы слать события в никуда.
    """
    client = httpx.AsyncClient(base_url=ai_service_url(), timeout=_STREAM_TIMEOUT)
    try:
        upstream = await client.send(
            client.build_request(
                "GET", _session_path(session_id, "/stream"), params={"after": after}
            ),
            stream=True,
        )
    except httpx.HTTPError as error:
        await client.aclose()
        raise _unavailable(error) from error

    if upstream.status_code != 200:
        # Отказ ИИ-сервиса передаётся обычным ответом, а не потоком. EventSource, получив
        # ответ не с типом text/event-stream, переподключаться не станет, а повтор запроса
        # к несуществующей сессии ничего бы и не дал.
        try:
            content = await upstream.aread()
        except httpx.HTTPError as error:
            raise _unavailable(error) from error
        finally:
            await upstream.aclose()
            await client.aclose()
        return Response(
            content=content,
            status_code=upstream.status_code,
            media_type=upstream.headers.get("content-type"),
        )

    async def relay() -> AsyncIterator[bytes]:
        try:
            async for chunk in upstream.aiter_bytes():
                yield chunk
        except httpx.HTTPError:
            # ИИ-сервис оборвал поток — остановлен либо перезапущен. Поток к браузеру
            # завершается, EventSource переподключается сам, а повторы событий страница
            # отбрасывает по порядковому номеру.
            return
        finally:
            # Закрытие выполняется и тогда, когда передачу отменил уход браузера. Отмена в
            # anyio действует на каждое ожидание внутри отменённой области, поэтому без
            # защиты закрытие было бы прервано, не начавшись.
            with anyio.CancelScope(shield=True):
                await upstream.aclose()
                await client.aclose()

    return StreamingResponse(
        relay(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # Отключает накопление ответа обратным прокси nginx, как и у /api/stream.
            "X-Accel-Buffering": "no",
        },
    )
