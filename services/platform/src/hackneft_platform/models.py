from datetime import datetime

from sqlalchemy import DateTime, Float, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from hackneft_platform.db import Base

# ORM-модель записи ПАК (данные о плотности и сере)


class PakData(Base):
    __tablename__ = "data_pak"

    id: Mapped[int] = mapped_column(primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, index=True)
    density: Mapped[float] = mapped_column(Float)
    sulfur: Mapped[float] = mapped_column(Float)

    # Дополнительные ограничения таблицы:
    # timestamp должен быть уникальным (не может быть двух записей с одним временем)
    __table_args__ = (
        UniqueConstraint("timestamp"),
    )
