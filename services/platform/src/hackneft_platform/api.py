from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from hackneft_platform.db import get_db, init_db
from hackneft_platform.models import DataSource, Measurement


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    init_db()
    yield


app = FastAPI(title="Hackneft Platform", lifespan=lifespan)
db_dependency = Depends(get_db)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


class MeasurementCreate(BaseModel):
    source: str
    tag: str
    value: float
    measured_at: datetime | None = None


class MeasurementResponse(BaseModel):
    id: int
    source: str
    tag: str
    value: float
    measured_at: datetime


@app.post("/measurements", response_model=MeasurementResponse, status_code=201)
def create_measurement(
    payload: MeasurementCreate,
    db: Session = db_dependency,
) -> MeasurementResponse:
    source = db.scalar(select(DataSource).where(
        DataSource.name == payload.source))
    if source is None:
        source = DataSource(name=payload.source)
        db.add(source)
        db.flush()

    measurement = Measurement(
        source=source,
        tag=payload.tag,
        value=payload.value,
        measured_at=payload.measured_at or datetime.now(UTC),
    )
    db.add(measurement)
    db.commit()
    db.refresh(measurement)

    return MeasurementResponse(
        id=measurement.id,
        source=source.name,
        tag=measurement.tag,
        value=measurement.value,
        measured_at=measurement.measured_at,
    )
