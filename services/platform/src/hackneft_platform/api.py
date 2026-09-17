from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from hackneft_platform.db import get_db, init_db
from hackneft_platform.models import PakData

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

# Схема входных данных для создания записи ПАК


class PakDataCreate(BaseModel):
    timestamp: datetime
    density: float
    sulfur: float

# Схема ответа: те же поля + идентификатор записи


class PakDataResponse(PakDataCreate):
    id: int

# Схема ответа со списком записей


class PakDataListResponse(BaseModel):
    items: list[PakDataResponse]

# POST-эндпоинт создания новой записи ПАК.


@app.post("/api/pak", response_model=PakDataResponse, status_code=201)
def create_pak_data(
    payload: PakDataCreate,
    db: Session = db_dependency,
) -> PakDataResponse:
    pak_data = PakData(
        timestamp=payload.timestamp,
        density=payload.density,
        sulfur=payload.sulfur,
    )
    db.add(pak_data)
    db.commit()
    db.refresh(pak_data)

    return PakDataResponse(
        id=pak_data.id,
        timestamp=pak_data.timestamp,
        density=pak_data.density,
        sulfur=pak_data.sulfur,
    )

# GET-эндпоинт получения последней (самой свежей) записи ПАК


@app.get("/api/pak", response_model=PakDataResponse)
def get_latest_pak_data(db: Session = db_dependency) -> PakDataResponse:
    pak_data = db.scalar(
        select(PakData).order_by(
            desc(PakData.timestamp), desc(PakData.id)).limit(1)
    )
    if pak_data is None:
        raise HTTPException(status_code=404, detail="No PAK data found")

    return PakDataResponse(
        id=pak_data.id,
        timestamp=pak_data.timestamp,
        density=pak_data.density,
        sulfur=pak_data.sulfur,
    )

# GET-эндпоинт получения всей истории записей ПАК


@app.get("/api/pak/history", response_model=PakDataListResponse)
def get_pak_history(db: Session = db_dependency) -> PakDataListResponse:
    pak_data = db.scalars(
        select(PakData).order_by(PakData.timestamp, PakData.id)
    ).all()

    return PakDataListResponse(
        items=[
            PakDataResponse(
                id=item.id,
                timestamp=item.timestamp,
                density=item.density,
                sulfur=item.sulfur,
            )
            for item in pak_data
        ]
    )


# Путь к каталогу со статикой (фронтенд: HTML/CSS/JS)
frontend_path = Path(__file__).parent / "static"
# Монтируем статику на корень "/", html=True — отдавать index.html по умолчанию
app.mount("/", StaticFiles(directory=frontend_path, html=True), name="frontend")
