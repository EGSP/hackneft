from datetime import datetime

from sqlalchemy import DateTime, Float, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from hackneft_platform.db import Base

# ORM-модель показания датчика (единая таблица для данных всех датчиков)


class SensorData(Base):
    __tablename__ = "sensor_data"

    id: Mapped[int] = mapped_column(primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, index=True)
    sensor_name: Mapped[str] = mapped_column(String, index=True)
    sensor_tag: Mapped[str] = mapped_column(String, index=True)
    value: Mapped[float] = mapped_column(Float)
    # Источник данных: откуда пришло показание (например: "pak", "scada", "manual")
    source: Mapped[str] = mapped_column(String, index=True)

    # Дополнительные ограничения таблицы:
    # для одного датчика не может быть двух записей с одним и тем же timestamp
    __table_args__ = (
        UniqueConstraint("timestamp", "sensor_tag"),
    )
