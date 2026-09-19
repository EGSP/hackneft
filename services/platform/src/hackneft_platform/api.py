from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from hackneft_platform import handlers  # noqa: F401  (регистрирует обработчиков событий)
from hackneft_platform.db import get_db, init_db
from hackneft_platform.events import SensorDataCreated, dispatcher
from hackneft_platform.models import SensorData, sensor_query

# Асинхронный контекстный менеджер жизненного цикла приложения:
# выполняется при старте (init_db) и после завершения (yield)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    init_db()
    yield

# Создание экземпляра FastAPI с заголовком и указанием lifespan
app = FastAPI(title="Hackneft Platform", lifespan=lifespan)
db_dependency = Depends(get_db)

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
        sensor_query.c.timestamp >= start,
        sensor_query.c.timestamp <= end,
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


# Путь к каталогу со статикой (фронтенд: HTML/CSS/JS)
frontend_path = Path(__file__).parent / "static"
# Монтируем статику на корень "/", html=True — отдавать index.html по умолчанию
app.mount("/", StaticFiles(directory=frontend_path, html=True), name="frontend")
