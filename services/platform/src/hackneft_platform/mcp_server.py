"""MCP-сервер платформы: инструменты агентов для запросов к показаниям датчиков.

ИИ-сервис от предметной области не зависит, поэтому инструменты чтения данных установки
предоставляет платформа, а ИИ-сервис подключается к ним как к любому серверу MCP
(Streamable HTTP, путь `/mcp`).

Инструмент показаний сделан в трёх вариантах, различающихся формой ответа, — чтобы сравнить,
какой из них даёт агенту достаточный ответ при меньшем расходе токенов:

- `sensor_series` — сырые точки ряда, прореженные до заданного числа;
- `sensor_summary` — сводка по окну без точек: последнее значение, минимум, максимум,
  среднее, изменение за окно;
- `sensor_resampled` — ряд, усреднённый по интервалам, общей таблицей для всех датчиков.

Общая часть — окно назад от курсора и последнее значение до окна для датчика без показаний в
окне — описана в sensor_queries.py.
"""

import os
from collections.abc import Callable
from datetime import timedelta
from typing import Annotated

from anyio import to_thread
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import Field
from sqlalchemy.orm import Session

from hackneft_platform.db import SessionLocal
from hackneft_platform.sensor_queries import (
    QueryError,
    SensorWindow,
    WindowQuery,
    buckets,
    fmt_time,
    query_window,
    search_catalog,
    summarize,
    thin,
)

_INSTRUCTIONS = """\
Показания датчиков установок АВТ и 24-2000 (гидроочистка), поточного анализатора (ПАК) и
лабораторных анализов (ЛИМС). Код датчика — приставка источника и тег: avt_*, ht_*, pack_*,
lims_*. Если код неизвестен, найди его инструментом sensor_catalog. Синонимы «ПАК» и «ЛИМС»
обозначают ряды серы в гидроочищенном дизельном топливе (норма — не более 10 мг/кг).

Окно отсчитывается назад от курсора. Без курсора используется текущий момент — время последнего
полученного показания. Показания позже курсора не выдаются. Время ЛИМС — момент отбора пробы;
результат становится известен примерно через 4 часа, поэтому последний анализ обычно старше
окна: тогда он приходит в поле last_known с собственной отметкой времени.
"""

server = MCPServer(name="hackneft-platform", instructions=_INSTRUCTIONS)

Sensors = Annotated[
    list[str],
    Field(
        min_length=1,
        description="Коды или синонимы датчиков, например ['ПАК', 'ЛИМС', 'ht_t6']",
    ),
]
Window = Annotated[
    float,
    Field(gt=0, le=44640, description="Длина окна в минутах, отсчитывается назад от курсора"),
]
Cursor = Annotated[
    str | None,
    Field(
        description=(
            "Конец окна, дата вида 2023-01-02T10:00. Не задан — время последнего показания"
        ),
    ),
]


async def _run[T](work: Callable[[Session], T]) -> T:
    """Выполняет выборку в потоке: драйвер SQLite синхронный и не должен занимать цикл событий."""

    def call() -> T:
        with SessionLocal() as db:
            try:
                return work(db)
            except QueryError as error:
                raise ToolError(str(error)) from error

    return await to_thread.run_sync(call)


def _header(query: WindowQuery) -> dict[str, object]:
    header: dict[str, object] = {
        "cursor": fmt_time(query.cursor),
        "window_start": fmt_time(query.start),
    }
    if query.unknown:
        header["unknown"] = query.unknown
        header["hint"] = "Неизвестные имена найди инструментом sensor_catalog."
    return header


def _sensor_head(sensor: SensorWindow) -> dict[str, object]:
    head: dict[str, object] = {"sensor": sensor.requested}
    if sensor.code != sensor.requested:
        head["code"] = sensor.code
    if sensor.title:
        head["title"] = sensor.title
    return head


def _last_known(sensor: SensorWindow) -> dict[str, object]:
    """Поля датчика без показаний в окне."""
    if sensor.last_before is None:
        return {"count": 0, "last_known": None}
    return {
        "count": 0,
        "last_known": [fmt_time(sensor.last_before.timestamp), round(sensor.last_before.value, 4)],
    }


@server.tool(
    description=(
        "Сырые показания датчиков в окне назад от курсора: пары [время, значение]. Длинный ряд "
        "прореживается равномерно до max_points точек. Если в окне нет показаний, возвращается "
        "последнее известное значение до окна (last_known)."
    )
)
async def sensor_series(
    sensors: Sensors,
    window_minutes: Window = 180,
    cursor: Cursor = None,
    max_points: Annotated[int, Field(ge=1, le=200, description="Предел точек на датчик")] = 36,
) -> dict[str, object]:
    def work(db: Session) -> dict[str, object]:
        query = query_window(db, sensors, window_minutes, cursor)
        items = []
        for sensor in query.sensors:
            item = _sensor_head(sensor)
            if sensor.points:
                shown = thin(sensor.points, max_points)
                item["count"] = len(sensor.points)
                if len(shown) < len(sensor.points):
                    item["thinned_to"] = len(shown)
                item["points"] = [[fmt_time(p.timestamp), round(p.value, 4)] for p in shown]
            else:
                item |= _last_known(sensor)
            items.append(item)
        return _header(query) | {"sensors": items}

    return await _run(work)


@server.tool(
    description=(
        "Сводка показаний датчиков в окне назад от курсора без сырых точек: число показаний, "
        "первое и последнее [время, значение], минимум, максимум, среднее и изменение за окно. "
        "Самый дешёвый способ узнать текущее значение и тенденцию. Если в окне нет показаний, "
        "возвращается последнее известное значение до окна (last_known)."
    )
)
async def sensor_summary(
    sensors: Sensors,
    window_minutes: Window = 180,
    cursor: Cursor = None,
) -> dict[str, object]:
    def work(db: Session) -> dict[str, object]:
        query = query_window(db, sensors, window_minutes, cursor)
        items = []
        for sensor in query.sensors:
            item = _sensor_head(sensor)
            item |= summarize(sensor.points) if sensor.points else _last_known(sensor)
            items.append(item)
        return _header(query) | {"sensors": items}

    return await _run(work)


@server.tool(
    description=(
        "Показания датчиков в окне назад от курсора, усреднённые по интервалам bucket_minutes, "
        "одной таблицей: строка — конец интервала, столбцы — датчики, null — нет показаний в "
        "интервале. Удобно для сравнения нескольких датчиков во времени. Датчики без показаний "
        "в окне перечислены в last_known с последним значением до окна."
    )
)
async def sensor_resampled(
    sensors: Sensors,
    window_minutes: Window = 180,
    cursor: Cursor = None,
    bucket_minutes: Annotated[
        float, Field(gt=0, description="Длина интервала усреднения в минутах")
    ] = 30,
) -> dict[str, object]:
    def work(db: Session) -> dict[str, object]:
        query = query_window(db, sensors, window_minutes, cursor)
        bucket = timedelta(minutes=bucket_minutes)
        if (query.cursor - query.start) / bucket > 200:
            raise QueryError("Больше 200 интервалов: увеличь bucket_minutes или сократи окно.")

        with_data = [sensor for sensor in query.sensors if sensor.points]
        columns = [
            sensor.requested if sensor.code == sensor.requested
            else f"{sensor.requested} ({sensor.code})"
            for sensor in with_data
        ]
        series = [buckets(s.points, query.start, query.cursor, bucket) for s in with_data]
        ends = sorted({end for values in series for end in values})
        rows = [
            [fmt_time(end), *(
                round(values[end], 4) if end in values else None for values in series
            )]
            for end in ends
        ]
        result = _header(query) | {
            "bucket_minutes": bucket_minutes,
            "columns": ["interval_end", *columns],
            "rows": rows,
        }
        missing = [sensor for sensor in query.sensors if not sensor.points]
        if missing:
            result["last_known"] = [
                _sensor_head(sensor) | _last_known(sensor) for sensor in missing
            ]
        return result

    return await _run(work)


@server.tool(
    description=(
        "Поиск датчиков по коду, названию или синониму: каждое слово запроса должно "
        "встречаться в коде или одном из имён. Возвращает коды и имена датчиков."
    )
)
async def sensor_catalog(
    query: Annotated[str, Field(min_length=1, description="Слова поиска, например 'сера'")],
    limit: Annotated[int, Field(ge=1, le=50)] = 20,
) -> dict[str, object]:
    def work(db: Session) -> dict[str, object]:
        found = search_catalog(db, query, limit)
        return {"found": len(found), "sensors": found}

    return await _run(work)


def create_mcp_app():  # type: ignore[no-untyped-def]
    """Приложение Streamable HTTP для монтирования в FastAPI.

    Сервер без состояния: каждый запрос независим, поэтому отдельные сессии MCP не хранятся.

    Защита от подмены DNS включена: запрос принимается только с допустимым заголовком Host, а
    запросы из браузера с чужим Origin отклоняются. Перечень имён задаётся переменной
    PLATFORM_MCP_ALLOWED_HOSTS через запятую; по умолчанию — локальный адрес и имя службы в
    сети compose, по которому к серверу обращается ИИ-сервис.
    """
    raw = os.getenv("PLATFORM_MCP_ALLOWED_HOSTS") or "localhost:*,127.0.0.1:*,platform:*"
    hosts = [host.strip() for host in raw.split(",") if host.strip()]
    return server.streamable_http_app(
        streamable_http_path="/",
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True, allowed_hosts=hosts
        ),
    )
