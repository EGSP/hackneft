"""MCP-сервер платформы: инструменты агентов для запросов к показаниям датчиков.

ИИ-сервис от предметной области не зависит, поэтому инструменты чтения данных установки
предоставляет платформа, а ИИ-сервис подключается к ним как к любому серверу MCP
(Streamable HTTP, путь `/mcp`).

Инструменты отдают только исходные данные и сами ничего не вычисляют: `sensor_series` —
показания как есть, `sensor_catalog` — коды и имена датчиков из справочника. Сводки и усреднения
отвергнуты по итогам сравнения на агентских сессиях: агент делал по ним выводы, которые
исходные точки не подтверждали (например, «тенденцию» по разности первого и последнего
значения зашумлённого ряда). Обработку данных агент выполняет сам, видя исходные показания.

Окно назад от курсора и последнее значение до окна для датчика без показаний в окне описаны в
sensor_queries.py.
"""

import os
from collections.abc import Callable
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
    fmt_time,
    query_window,
    search_catalog,
)

_INSTRUCTIONS = """\
Показания датчиков установок АВТ и 24-2000 (гидроочистка), поточного анализатора (ПАК) и
лабораторных анализов (ЛИМС). Код датчика — приставка источника и тег: avt_*, ht_*, pack_*,
lims_*. Если код неизвестен, найди его инструментом sensor_catalog; по умолчанию он ищет только
среди датчиков с синонимами, для поиска по всем передай only_with_synonyms=false.
Синонимы «ПАК» и «ЛИМС» обозначают ряды серы в гидроочищенном дизельном топливе (порог — не
более 10 мг/кг).

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
        "Исходные показания датчиков в окне назад от курсора: пары [время, значение] без "
        "обработки. Если показаний в окне больше max_points, возвращаются последние max_points "
        "и поле truncated; count — число показаний в окне. Если в окне нет показаний, "
        "возвращается последнее известное значение до окна (last_known)."
    )
)
async def sensor_series(
    sensors: Sensors,
    window_minutes: Window = 180,
    cursor: Cursor = None,
    max_points: Annotated[
        int, Field(ge=1, le=500, description="Предел точек на датчик, берутся последние")
    ] = 72,
) -> dict[str, object]:
    def work(db: Session) -> dict[str, object]:
        query = query_window(db, sensors, window_minutes, cursor)
        items = []
        for sensor in query.sensors:
            item = _sensor_head(sensor)
            if sensor.points:
                shown = sensor.points[-max_points:]
                item["count"] = len(sensor.points)
                if len(shown) < len(sensor.points):
                    item["truncated"] = (
                        f"показаны последние {len(shown)} из {len(sensor.points)}; "
                        "для более ранних сдвинь курсор назад"
                    )
                item["points"] = [[fmt_time(p.timestamp), round(p.value, 4)] for p in shown]
            else:
                item |= _last_known(sensor)
            items.append(item)
        return _header(query) | {"sensors": items}

    return await _run(work)


@server.tool(
    description=(
        "Поиск датчиков по коду, названию или синониму. По умолчанию (only_with_synonyms=true) "
        "ищет только среди датчиков, выделенных синонимами по назначению (например «ПАК», "
        "«ЛИМС», «Сера в сырье»); без запроса перечисляет их все. Если нужного датчика нет, "
        "повтори с only_with_synonyms=false — поиск пойдёт по всем датчикам установок АВТ и "
        "24-2000, ПАК и ЛИМС. Датчики упорядочены по числу совпавших слов (matched)."
    )
)
async def sensor_catalog(
    query: Annotated[
        str, Field(description="Слова поиска, например 'сера'. Пусто — все прошедшие отбор")
    ] = "",
    only_with_synonyms: Annotated[
        bool,
        Field(description="Только датчики с синонимами сверх исходных названий"),
    ] = True,
    limit: Annotated[int, Field(ge=1, le=50)] = 20,
) -> dict[str, object]:
    def work(db: Session) -> dict[str, object]:
        found = search_catalog(db, query, limit, only_with_synonyms)
        result: dict[str, object] = {"found": len(found), "sensors": found}
        if not found and only_with_synonyms:
            result["hint"] = (
                "Среди датчиков с синонимами не найдено: повтори с only_with_synonyms=false."
            )
        return result

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
