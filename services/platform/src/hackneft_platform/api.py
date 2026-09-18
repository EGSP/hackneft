from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from hackneft_platform.db import get_db, init_db
from hackneft_platform.events import ai_agent_event_handler
from hackneft_platform.models import SensorData
from hackneft_platform.observers import sensor_tag_observer

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

# Схема входных данных для создания записи показания датчика


class SensorDataCreate(BaseModel):
    timestamp: datetime
    sensor_name: str
    sensor_tag: str
    value: float
    source: str

# Схема ответа: те же поля + идентификатор записи


class SensorDataResponse(SensorDataCreate):
    id: int

# Схема ответа со списком записей


class SensorDataListResponse(BaseModel):
    items: list[SensorDataResponse]

# POST-эндпоинт создания новой записи показания датчика.


@app.post("/api/sensor-data", response_model=SensorDataResponse, status_code=201)
def create_sensor_data(
    payload: SensorDataCreate,
    background_tasks: BackgroundTasks,
    db: Session = db_dependency,
) -> SensorDataResponse:
    sensor_data = SensorData(
        timestamp=payload.timestamp,
        sensor_name=payload.sensor_name,
        sensor_tag=payload.sensor_tag,
        value=payload.value,
        source=payload.source,
    )
    db.add(sensor_data)
    db.commit()
    db.refresh(sensor_data)

    sensor_tag_observer.notify(sensor_data.sensor_tag)

    background_tasks.add_task(
        ai_agent_event_handler.handle_sensor_data_created,
        event_id=f"sensor-data:{sensor_data.id}",
        data_id=sensor_data.id,
        timestamp=sensor_data.timestamp,
        sensor_name=sensor_data.sensor_name,
        sensor_tag=sensor_data.sensor_tag,
        value=sensor_data.value,
        source=sensor_data.source,
    )

    return SensorDataResponse(
        id=sensor_data.id,
        timestamp=sensor_data.timestamp,
        sensor_name=sensor_data.sensor_name,
        sensor_tag=sensor_data.sensor_tag,
        value=sensor_data.value,
        source=sensor_data.source,
    )

# GET-эндпоинт получения последней (самой свежей) записи показания датчика


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
        sensor_name=sensor_data.sensor_name,
        sensor_tag=sensor_data.sensor_tag,
        value=sensor_data.value,
        source=sensor_data.source,
    )

# GET-эндпоинт получения записей показаний датчиков за интервал времени


@app.get("/api/sensor-data/range", response_model=SensorDataListResponse)
def get_sensor_data_range(
    start: datetime,
    end: datetime,
    sensor_tag: str | None = None,
    db: Session = db_dependency,
) -> SensorDataListResponse:
    query = select(SensorData).where(
        SensorData.timestamp >= start,
        SensorData.timestamp <= end,
    )
    if sensor_tag is not None:
        query = query.where(SensorData.sensor_tag == sensor_tag)
    query = query.order_by(SensorData.timestamp, SensorData.id)

    sensor_data = db.scalars(query).all()

    return SensorDataListResponse(
        items=[
            SensorDataResponse(
                id=item.id,
                timestamp=item.timestamp,
                sensor_name=item.sensor_name,
                sensor_tag=item.sensor_tag,
                value=item.value,
                source=item.source,
            )
            for item in sensor_data
        ]
    )

# GET-эндпоинт получения всей истории записей показаний датчиков


@app.get("/api/sensor-data/history", response_model=SensorDataListResponse)
def get_sensor_data_history(db: Session = db_dependency) -> SensorDataListResponse:
    sensor_data = db.scalars(
        select(SensorData).order_by(SensorData.timestamp, SensorData.id)
    ).all()

    return SensorDataListResponse(
        items=[
            SensorDataResponse(
                id=item.id,
                timestamp=item.timestamp,
                sensor_name=item.sensor_name,
                sensor_tag=item.sensor_tag,
                value=item.value,
                source=item.source,
            )
            for item in sensor_data
        ]
    )


# Путь к каталогу со статикой (фронтенд: HTML/CSS/JS)
frontend_path = Path(__file__).parent / "static"
# Монтируем статику на корень "/", html=True — отдавать index.html по умолчанию
app.mount("/", StaticFiles(directory=frontend_path, html=True), name="frontend")
