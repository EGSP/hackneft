from datetime import datetime

from sqlalchemy import Column, DateTime, Float, MetaData, String, Table, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from hackneft_platform.db import Base

# ORM-модель измерения (единая таблица для данных всех датчиков).
# Содержит только сами измерения: время, код датчика, значение — и источник данных
# (откуда пришло показание, например "pak"/"scada"/"manual"): это свойство самого
# измерения, а не терминология, поэтому оно остаётся здесь, в отличие от имени датчика.
# Человекочитаемые имена и синонимы датчика сюда не входят — см. SensorName и sensor_query.


class SensorData(Base):
    __tablename__ = "sensor_data"

    id: Mapped[int] = mapped_column(primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, index=True)
    sensor_code: Mapped[str] = mapped_column(String, index=True)
    value: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String, index=True)

    # Для одного датчика не может быть двух записей с одним и тем же timestamp.
    # Повторная доставка одного показания — штатная ситуация для потоковых данных,
    # см. обработку IntegrityError в api.py.
    __table_args__ = (
        UniqueConstraint("timestamp", "sensor_code"),
    )


# Справочник имён: код датчика и любое его имя — сам код, обозначение «ПАК», «ЛИМС»,
# описательное название. Исходный код тоже хранится здесь как одно из имён (строка
# sensor_code == name), поэтому поиск по коду и по синониму — одинаковый запрос
# к sensor_query, а не два разных случая.


class SensorName(Base):
    __tablename__ = "sensor_names"

    sensor_code: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, primary_key=True)


# Представление sensor_query — соединение sensor_data и sensor_names по коду датчика.
# Это не таблица: создаётся отдельной командой CREATE VIEW при инициализации БД
# (db.py:_create_sensor_query_view), а не через Base.metadata.create_all.
#
# Объявлено здесь как SQLAlchemy Table только для чтения, в СВОЁМ MetaData (не в
# Base.metadata) — иначе create_all попытался бы создать его как обычную таблицу и упал
# бы на конфликте имён с уже существующим представлением.
_view_metadata = MetaData()

sensor_query = Table(
    "sensor_query",
    _view_metadata,
    Column("timestamp", DateTime),
    Column("sensor_code", String),
    Column("sensor_name", String),
    Column("value", Float),
)
