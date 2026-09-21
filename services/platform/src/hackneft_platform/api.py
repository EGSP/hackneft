import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from hackneft_platform import handlers  # noqa: F401  (регистрирует обработчиков событий)
from hackneft_platform.catalog import (
    SULFUR_LIMIT_MG_KG,
    SULFUR_LIMS_NAME,
    SULFUR_NAMES,
    SULFUR_PAK_NAME,
)
from hackneft_platform.db import get_db, init_db
from hackneft_platform.events import SensorDataCreated, dispatcher
from hackneft_platform.handlers.sulfur_stream import (
    SulfurReading,
    reset_tracked_codes,
    subscribers,
)
from hackneft_platform.models import SensorData, SensorName, sensor_query

# Асинхронный контекстный менеджер жизненного цикла приложения:
# выполняется при старте (init_db) и после завершения (yield)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    init_db()
    yield

# Создание экземпляра FastAPI с заголовком и указанием lifespan
app = FastAPI(title="Hackneft Platform", lifespan=lifespan)
db_dependency = Depends(get_db)
# Повторяющийся параметр запроса: ?name=ПАК&name=ЛИМС. Объявлен здесь, а не прямо
# в сигнатуре эндпоинта: вызов в значении по умолчанию выполняется один раз при
# импорте модуля, и один и тот же изменяемый объект достался бы всем запросам.
names_query = Query(default=[])

# Эндпоинт проверки работоспособности сервиса


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}

# Схема входных данных для создания записи показания датчика.
# Только измерение: время, код датчика, значение, источник. Имя датчика сюда не входит —
# оно регистрируется отдельно, в справочнике sensor_names (см. models.py).


class SensorDataCreate(BaseModel):
    timestamp: datetime
    sensor_code: str
    value: float
    source: str

# Схема ответа: те же поля + идентификатор записи


class SensorDataResponse(SensorDataCreate):
    id: int

# Схема ответа со списком "сырых" записей измерений (без имени датчика)


class SensorDataListResponse(BaseModel):
    items: list[SensorDataResponse]

# Схема одной строки представления sensor_query: измерение вместе с ОДНИМ из имён
# датчика. Одна и та же запись измерения (timestamp, sensor_code) может встретиться
# в представлении несколько раз — по разу на каждое зарегистрированное имя. Фильтр
# `name` в /range выбирает конкретное имя и тем самым убирает дублирование.


class SensorQueryItem(BaseModel):
    timestamp: datetime
    sensor_code: str
    sensor_name: str
    value: float


class SensorQueryListResponse(BaseModel):
    items: list[SensorQueryItem]

# POST-эндпоинт создания новой записи показания датчика.


@app.post("/api/sensor-data", response_model=SensorDataResponse, status_code=201)
def create_sensor_data(
    payload: SensorDataCreate,
    background_tasks: BackgroundTasks,
    response: Response,
    db: Session = db_dependency,
) -> SensorDataResponse:
    sensor_data = SensorData(
        timestamp=payload.timestamp,
        sensor_code=payload.sensor_code,
        value=payload.value,
        source=payload.source,
    )
    
    # После создания объекта SensorData добавляем его в сессию SQLAlchemy, чтобы подготовить к сохранению в базе данных.
    db.add(sensor_data)

    try:
        db.commit()
    except IntegrityError:
        # UNIQUE(timestamp, sensor_code): повторная доставка одного и того же показания —
        # штатная ситуация для потоковых данных, а не ошибка. Отдаём уже сохранённую
        # запись с кодом 200 вместо падения в 500.
        db.rollback()
        existing = db.scalar(
            select(SensorData).where(
                SensorData.timestamp == payload.timestamp,
                SensorData.sensor_code == payload.sensor_code,
            )
        )
        if existing is None:
            # IntegrityError по другой причине (не по этому ограничению) — не подменяем
            # её ложным идемпотентным ответом.
            raise HTTPException(
                status_code=409, detail="Конфликт при сохранении записи"
            ) from None

        response.status_code = 200
        return SensorDataResponse(
            id=existing.id,
            timestamp=existing.timestamp,
            sensor_code=existing.sensor_code,
            value=existing.value,
            source=existing.source,
        )

    # После успешного сохранения в БД обновляем объект из БД, чтобы получить сгенерированный идентификатор
    db.refresh(sensor_data)

    # Публикация события уходит в фон: ответ клиенту не должен ждать,
    # пока отработают все подписчики диспетчера (в т.ч. будущие, с сетевыми
    # вызовами). Подписчики сами решают, что делать с событием — эндпоинт
    # не знает, сколько их и что они делают.
    background_tasks.add_task(
        dispatcher.publish,
        SensorDataCreated(
            event_id=f"sensor-data:{sensor_data.id}",
            data_id=sensor_data.id,
            timestamp=sensor_data.timestamp,
            sensor_code=sensor_data.sensor_code,
            value=sensor_data.value,
            source=sensor_data.source,
        ),
    )

    return SensorDataResponse(
        id=sensor_data.id,
        timestamp=sensor_data.timestamp,
        sensor_code=sensor_data.sensor_code,
        value=sensor_data.value,
        source=sensor_data.source,
    )

# Схема запроса пакетной записи показаний. Поток телеметрии приходит отметками времени:
# на одну отметку приходится по одному показанию с каждого датчика установки, то есть
# несколько десятков записей. Поодиночке это столько же HTTP-запросов, поэтому пакет
# принимается одним запросом и сохраняется одной транзакцией.


class SensorDataBulkCreate(BaseModel):
    items: list[SensorDataCreate]

# Схема ответа на пакетную запись. Возвращаются не сами записи, а их количества:
# отправителю потока нужно знать, сколько показаний принято и сколько отброшено как
# повторные, а не идентификаторы каждой строки.


class SensorDataBulkResponse(BaseModel):
    accepted: int
    duplicates: int

# POST-эндпоинт пакетной записи показаний.
# Повторная доставка обрабатывается так же, как в одиночном эндпоинте: нарушение
# UNIQUE(timestamp, sensor_code) — штатная ситуация потока, а не ошибка. Каждая запись
# вставляется во вложенной транзакции (SAVEPOINT), поэтому повтор одной записи не
# отменяет остальные записи пакета.


@app.post("/api/sensor-data/bulk", response_model=SensorDataBulkResponse, status_code=201)
def create_sensor_data_bulk(
    payload: SensorDataBulkCreate,
    background_tasks: BackgroundTasks,
    db: Session = db_dependency,
) -> SensorDataBulkResponse:
    saved: list[SensorData] = []
    duplicates = 0

    for item in payload.items:
        sensor_data = SensorData(
            timestamp=item.timestamp,
            sensor_code=item.sensor_code,
            value=item.value,
            source=item.source,
        )
        try:
            with db.begin_nested():
                db.add(sensor_data)
                db.flush()
        except IntegrityError:
            duplicates += 1
            continue
        saved.append(sensor_data)

    db.commit()

    # События публикуются по одному на запись: подписчики диспетчера рассчитаны на
    # отдельное показание и о пакете ничего не знают. Публикация уходит в фон по той же
    # причине, что и в одиночном эндпоинте, — ответ не должен ждать подписчиков.
    for sensor_data in saved:
        background_tasks.add_task(
            dispatcher.publish,
            SensorDataCreated(
                event_id=f"sensor-data:{sensor_data.id}",
                data_id=sensor_data.id,
                timestamp=sensor_data.timestamp,
                sensor_code=sensor_data.sensor_code,
                value=sensor_data.value,
                source=sensor_data.source,
            ),
        )

    return SensorDataBulkResponse(accepted=len(saved), duplicates=duplicates)

# GET-эндпоинт получения последней (самой свежей) записи показания датчика.
# Читает sensor_data напрямую (не через sensor_query) — здесь не нужно имя датчика,
# и через представление одно измерение размножилось бы на все свои синонимы.


@app.get("/api/sensor-data", response_model=SensorDataResponse)
def get_latest_sensor_data(db: Session = db_dependency) -> SensorDataResponse:
    sensor_data = db.scalar(
        select(SensorData).order_by(
            desc(SensorData.timestamp), desc(SensorData.id)).limit(1)
    )
    if sensor_data is None:
        raise HTTPException(status_code=404, detail="No sensor data found")

    return SensorDataResponse(
        id=sensor_data.id,
        timestamp=sensor_data.timestamp,
        sensor_code=sensor_data.sensor_code,
        value=sensor_data.value,
        source=sensor_data.source,
    )

# GET-эндпоинт получения записей показаний датчиков за интервал времени.
# Запрос — к представлению sensor_query, а не к sensor_data: `name` может быть как
# кодом датчика, так и любым его синонимом (оба зарегистрированы в sensor_names),
# запрос их не различает — `?name=F6` и `?name=ПАК` для одного датчика вернут
# одни и те же измерения.


@app.get("/api/sensor-data/range", response_model=SensorQueryListResponse)
def get_sensor_data_range(
    start: datetime,
    end: datetime,
    name: str | None = None,
    db: Session = db_dependency,
) -> SensorQueryListResponse:
    query = select(sensor_query).where(
        sensor_query.c.timestamp >= to_naive_local(start),
        sensor_query.c.timestamp <= to_naive_local(end),
    )
    if name is not None:
        query = query.where(sensor_query.c.sensor_name == name)
    query = query.order_by(
        sensor_query.c.timestamp, sensor_query.c.sensor_code, sensor_query.c.sensor_name
    )

    rows = db.execute(query).mappings().all()

    return SensorQueryListResponse(items=[SensorQueryItem(**row) for row in rows])

# GET-эндпоинт получения всей истории записей показаний датчиков.
# Тоже через sensor_query: каждое измерение приходит с одним из своих имён,
# так что потребитель (например, фронтенд) может отфильтровать по конкретному
# человекочитаемому имени, не заботясь о том, код это или синоним.


@app.get("/api/sensor-data/history", response_model=SensorQueryListResponse)
def get_sensor_data_history(db: Session = db_dependency) -> SensorQueryListResponse:
    query = select(sensor_query).order_by(
        sensor_query.c.timestamp, sensor_query.c.sensor_code, sensor_query.c.sensor_name
    )
    rows = db.execute(query).mappings().all()

    return SensorQueryListResponse(items=[SensorQueryItem(**row) for row in rows])


def to_naive_local(value: datetime) -> datetime:
    """Приводит границу периода к виду, в котором метки времени хранятся в базе.

    Показания приходят от источников с местным временем и записываются без указания
    часового пояса (см. модель SensorData). Граница же может прийти со смещением:
    браузер, например, отправляет её в UTC. Сравнение naive-значения из базы с aware
    границей дало бы сдвиг на величину смещения и отсекло часть данных, поэтому граница
    переводится в местное время, а смещение отбрасывается.
    """
    if value.tzinfo is None:
        return value
    return value.astimezone().replace(tzinfo=None)


# GET-эндпоинт выборки за период сразу по нескольким именам датчиков.
# От /range отличается тем, что имён можно указать несколько: ?name=ПАК&name=ЛИМС.
# Каждое имя — любая строка из sensor_names: сам код датчика либо любой его синоним,
# представление sensor_query их не различает. Ответ содержит поле `requested_name` —
# то имя из запроса, по которому строка найдена; по нему потребитель раскладывает
# общий список на ряды, не выясняя заново, какой синоним какому коду принадлежит.


class SensorViewItem(BaseModel):
    timestamp: datetime
    sensor_code: str
    sensor_name: str
    requested_name: str
    value: float


class SensorViewResponse(BaseModel):
    items: list[SensorViewItem]


@app.get("/api/sensor-data/view", response_model=SensorViewResponse)
def get_sensor_data_view(
    start: datetime,
    end: datetime,
    name: list[str] = names_query,
    db: Session = db_dependency,
) -> SensorViewResponse:
    if not name:
        raise HTTPException(
            status_code=400,
            detail="Требуется хотя бы одно имя датчика в параметре name",
        )

    # Запрошенные имена сопоставляются кодам датчиков одним обращением к справочнику.
    # Дальше выборка измерений идёт по кодам, а не по именам: измерение датчика,
    # у которого зарегистрировано несколько синонимов, иначе вернулось бы из
    # представления по разу на каждый синоним.
    requested = list(dict.fromkeys(name))
    code_rows = db.execute(
        select(SensorName.name, SensorName.sensor_code).where(
            SensorName.name.in_(requested)
        )
    ).all()
    code_by_name = {row.name: row.sensor_code for row in code_rows}

    unknown = [item for item in requested if item not in code_by_name]
    if unknown:
        raise HTTPException(
            status_code=404,
            detail="Имена датчиков не зарегистрированы: " + ", ".join(unknown),
        )

    codes = set(code_by_name.values())
    query = (
        select(
            sensor_query.c.timestamp,
            sensor_query.c.sensor_code,
            sensor_query.c.value,
        )
        .where(
            sensor_query.c.timestamp >= to_naive_local(start),
            sensor_query.c.timestamp <= to_naive_local(end),
            sensor_query.c.sensor_code.in_(codes),
            # Строка представления, где имя совпадает с кодом, есть у каждого датчика
            # всегда (см. db.py:_seed_sensor_names), поэтому такое условие оставляет
            # ровно одну строку на измерение независимо от числа синонимов.
            sensor_query.c.sensor_name == sensor_query.c.sensor_code,
        )
        .order_by(sensor_query.c.timestamp, sensor_query.c.sensor_code)
    )
    rows = db.execute(query).mappings().all()

    # Одно измерение попадает в ответ столько раз, сколько имён одного и того же
    # датчика перечислено в запросе: потребитель запросил именно эти ряды.
    items = [
        SensorViewItem(
            timestamp=row["timestamp"],
            sensor_code=row["sensor_code"],
            sensor_name=requested_name,
            requested_name=requested_name,
            value=row["value"],
        )
        for row in rows
        for requested_name in requested
        if code_by_name[requested_name] == row["sensor_code"]
    ]

    return SensorViewResponse(items=items)


# Справочник синонимов: чтение и правка таблицы sensor_names.
# Строка, где имя совпадает с кодом, служит признаком существования датчика и удалению
# не подлежит: без неё представление sensor_query перестанет отдавать его измерения.


class SensorNameItem(BaseModel):
    sensor_code: str
    name: str
    is_code: bool
    # Имя, по которому главная страница запрашивает ряд серы. Такое имя тоже защищено
    # от удаления, поэтому интерфейс обозначает его и не предлагает кнопку удаления.
    in_use: bool


class SensorNameListResponse(BaseModel):
    items: list[SensorNameItem]


class SensorNameCreate(BaseModel):
    sensor_code: str
    name: str


@app.get("/api/sensor-names", response_model=SensorNameListResponse)
def list_sensor_names(db: Session = db_dependency) -> SensorNameListResponse:
    rows = (
        db.execute(select(SensorName).order_by(SensorName.sensor_code, SensorName.name))
        .scalars()
        .all()
    )

    return SensorNameListResponse(
        items=[
            SensorNameItem(
                sensor_code=row.sensor_code,
                name=row.name,
                is_code=row.sensor_code == row.name,
                in_use=row.name in SULFUR_NAMES,
            )
            for row in rows
        ]
    )


@app.post("/api/sensor-names", response_model=SensorNameItem, status_code=201)
def create_sensor_name(
    payload: SensorNameCreate,
    db: Session = db_dependency,
) -> SensorNameItem:
    code = payload.sensor_code.strip()
    name = payload.name.strip()
    if not code or not name:
        raise HTTPException(
            status_code=400, detail="Код датчика и синоним не могут быть пустыми"
        )

    # Синоним добавляется только существующему датчику: строка (код, код) заводится
    # при инициализации БД из перечня catalog.KNOWN_SENSOR_CODES.
    known = db.scalar(
        select(SensorName).where(
            SensorName.sensor_code == code, SensorName.name == code
        )
    )
    if known is None:
        raise HTTPException(status_code=404, detail=f"Датчик {code} не зарегистрирован")

    # Имя указывает ровно на один датчик. Иначе запрос ряда по имени стал бы
    # двусмысленным: и выборка за период, и подписчик потока разрешают имя в один код,
    # и при двух привязках выбор зависел бы от порядка строк в справочнике. Поэтому
    # имя, уже принадлежащее другому датчику, переносится, а не добавляется вторым.
    # Так же выполняется и смена источника ряда на главной странице: имя «ПАК» или
    # «ЛИМС» переносится на новый код, удалять его при этом не требуется (ниже, в
    # delete_sensor_name, удаление таких имён отклоняется).
    taken = db.scalar(select(SensorName).where(SensorName.name == name))
    if taken is not None:
        if taken.sensor_code == code:
            raise HTTPException(
                status_code=409, detail=f"Синоним {name} у датчика {code} уже есть"
            )
        if taken.sensor_code == taken.name:
            # Строка, где имя совпадает с кодом, обозначает сам датчик: перенести её
            # означало бы переименовать датчик, а справочник имён для этого не служит.
            raise HTTPException(
                status_code=409,
                detail=f"{name} — код другого датчика, синонимом он быть не может",
            )
        db.delete(taken)
        db.flush()

    db.add(SensorName(sensor_code=code, name=name))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409, detail=f"Синоним {name} у датчика {code} уже есть"
        ) from None

    # Подписчик потока держит соответствие «имя ряда — код датчика» в памяти. Новая
    # строка справочника может переносить имя ряда на другой датчик, поэтому запомненное
    # соответствие после правки недействительно.
    reset_tracked_codes()

    return SensorNameItem(
        sensor_code=code, name=name, is_code=code == name, in_use=name in SULFUR_NAMES
    )


@app.delete("/api/sensor-names", status_code=204)
def delete_sensor_name(
    sensor_code: str,
    name: str,
    db: Session = db_dependency,
) -> Response:
    if sensor_code == name:
        raise HTTPException(
            status_code=400,
            detail="Имя, совпадающее с кодом датчика, удалить нельзя: оно обозначает сам датчик",
        )

    # Имена рядов серы удалению не подлежат по той же причине: по ним главная страница
    # запрашивает измерения и подписывает ряды, и без строки справочника имя перестаёт
    # разрешаться в код датчика. Перенести ряд на другой датчик можно, добавив это имя
    # нужному коду, а не удалив прежнюю строку.
    if name in SULFUR_NAMES:
        raise HTTPException(
            status_code=400,
            detail=f"Имя {name} используется главной страницей и удалению не подлежит",
        )

    existing = db.scalar(
        select(SensorName).where(
            SensorName.sensor_code == sensor_code, SensorName.name == name
        )
    )
    if existing is None:
        raise HTTPException(status_code=404, detail="Такого синонима нет")

    db.delete(existing)
    db.commit()
    reset_tracked_codes()

    return Response(status_code=204)


# Параметры главной страницы: имена двух рядов серы и норма. Отдаются интерфейсу, чтобы
# он не повторял у себя значения, объявленные в catalog.py.
#
# Отдаются именно имена из справочника sensor_names, а не коды датчиков: страница и
# запрашивает ряды по именам (/api/sensor-data/view), и подписывает их этими же именами
# на графике. Код датчика, стоящий за именем, интерфейсу не нужен и ему не сообщается —
# соответствие «имя — код» целиком остаётся делом справочника.


class SulfurSettingsResponse(BaseModel):
    pak_name: str
    lims_name: str
    limit: float


@app.get("/api/sulfur/settings", response_model=SulfurSettingsResponse)
def get_sulfur_settings() -> SulfurSettingsResponse:
    return SulfurSettingsResponse(
        pak_name=SULFUR_PAK_NAME,
        lims_name=SULFUR_LIMS_NAME,
        limit=SULFUR_LIMIT_MG_KG,
    )


# Поток событий SSE. Соединение регистрирует собственную очередь в множестве
# подписчиков (handlers/sulfur_stream.py) и удаляет её при разрыве. В поток попадают
# только показания серы: подписчик отбирает их по именам рядов из справочника. Вместе
# с полями события передаётся поле `name` — имя ряда, к которому показание относится.
# Получатель раскладывает поток на ряды по нему, а не по коду датчика: по именам он
# запрашивал и саму выборку за период.

STREAM_KEEPALIVE_SECONDS = 15


@app.get("/api/stream")
async def stream_sensor_events(request: Request) -> StreamingResponse:
    queue: asyncio.Queue[SulfurReading] = asyncio.Queue(maxsize=100)
    subscribers.add(queue)

    async def events() -> AsyncIterator[str]:
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    reading = await asyncio.wait_for(
                        queue.get(), timeout=STREAM_KEEPALIVE_SECONDS
                    )
                except TimeoutError:
                    # Комментарий SSE: промежуточный узел не должен разорвать
                    # соединение за время бездействия.
                    yield ": keep-alive\n\n"
                    continue

                payload = asdict(reading.event)
                payload["timestamp"] = reading.event.timestamp.isoformat()
                payload["name"] = reading.name
                yield "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"
        finally:
            subscribers.discard(queue)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # Отключает накопление ответа обратным прокси nginx: без этого события
            # доходят до браузера порциями, а не по мере публикации.
            "X-Accel-Buffering": "no",
        },
    )


# Путь к каталогу со статикой: собранный Vite интерфейс, см. services/platform/web.
frontend_path = Path(__file__).parent / "static"
frontend_index = frontend_path / "index.html"

# Срок хранения в кеше браузера. Имя файла сборки содержит хеш её содержимого, поэтому
# такой файл можно хранить сколь угодно долго: после пересборки страница запрашивает файл
# с другим именем. Сама же index.html имя не меняет и перечисляет имена файлов сборки,
# поэтому браузер обязан спрашивать её у сервера при каждом открытии. Без этого после
# пересборки интерфейса он продолжит выполнять прежний код и запрашивать файлы, которых
# в новой сборке уже нет.
INDEX_CACHE_CONTROL = "no-cache"
ASSET_CACHE_CONTROL = "public, max-age=31536000, immutable"


# Маршрутизация страниц выполняется в браузере (react-router), поэтому на любой путь,
# не совпавший с объявленными выше, сервер отдаёт index.html — иначе переход по адресу
# вида /synonyms и перезагрузка такой страницы завершились бы ответом 404. Обработчик
# объявлен последним: шаблон {full_path:path} совпадает с чем угодно, и объявленный
# раньше он перехватил бы обращения к /api.
@app.get("/{full_path:path}", include_in_schema=False)
def serve_frontend(full_path: str) -> FileResponse:
    # Необъявленный путь API — ошибка обращения, а не адрес страницы: клиент ждёт
    # здесь ответ 404 с описанием, а не разметку интерфейса.
    if full_path.startswith("api/"):
        raise HTTPException(status_code=404, detail="Эндпоинт не найден")

    # Файлы сборки (код, таблицы стилей, шрифты) лежат в подкаталоге assets и хранятся
    # в кеше долго; всё остальное, что попало в каталог статики, — на общих основаниях.
    candidate = frontend_path / full_path
    if full_path and candidate.is_file():
        cache_control = (
            ASSET_CACHE_CONTROL if full_path.startswith("assets/") else INDEX_CACHE_CONTROL
        )
        return FileResponse(candidate, headers={"Cache-Control": cache_control})

    # Отсутствующий файл сборки — не адрес страницы. Браузер, сохранивший прежнюю
    # index.html, запрашивает файлы предыдущей сборки; получив вместо кода разметку
    # с ответом 200, он отказывается её исполнять, и страница остаётся пустой без
    # объяснения причины. Ответ 404 делает положение явным, а после обновления
    # index.html (она отдаётся с no-cache) запрашиваются уже файлы новой сборки.
    if full_path.startswith("assets/"):
        raise HTTPException(status_code=404, detail="Файл сборки не найден")
    if not frontend_index.is_file():
        raise HTTPException(
            status_code=503,
            detail="Интерфейс не собран: выполните npm run build в services/platform/web",
        )
    return FileResponse(frontend_index, headers={"Cache-Control": INDEX_CACHE_CONTROL})
