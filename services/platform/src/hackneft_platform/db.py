from collections.abc import Generator

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from hackneft_platform.config import database_path, load_env_file

# Базовый класс для всех ORM-моделей (новый стиль SQLAlchemy 2.0)


class Base(DeclarativeBase):
    pass


# Путь к файлу базы задаётся переменной PLATFORM_DATABASE_PATH так же, как в ИИ-сервисе:
# относительный путь отсчитывается от каталога сервиса, а не от текущего каталога процесса.
load_env_file()
_database_file = database_path()
_database_file.parent.mkdir(parents=True, exist_ok=True)
database_url = f"sqlite:///{_database_file.as_posix()}"

# Создаём движок SQLAlchemy.
# SQLite-соединения используются из разных потоков FastAPI.
# Для остальных СУБД дополнительные аргументы не требуются.
engine = create_engine(
    database_url,
    connect_args={"check_same_thread": False} if database_url.startswith("sqlite") else {},
)


# Параметры каждого нового соединения SQLite.
# journal_mode=WAL: чтение не блокирует запись и запись не блокирует чтение, поэтому
# запросы веб-интерфейса не мешают потоку показаний от агрегатора. Режим сохраняется
# в файле базы, повторная установка безопасна.
# busy_timeout: сколько миллисекунд ждать снятия блокировки другим писателем, прежде чем
# вернуть «database is locked». Стандартных 5 секунд не хватало при подаче крупными пакетами.
@event.listens_for(engine, "connect")
def _configure_sqlite(dbapi_connection, _connection_record) -> None:  # type: ignore[no-untyped-def]
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.close()


# Фабрика сессий: autoflush=False — не сбрасывать автоматически изменения,
# autocommit=False — не фиксировать транзакции автоматически (управляем вручную)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

# Зависимость FastAPI: открывает сессию на время запроса и закрывает её после.
# Используется как Depends(get_db) в эндпоинтах.


def get_db() -> Generator[Session, None, None]:
    with SessionLocal() as session:
        yield session


# Инициализация схемы БД. Порядок важен: сначала таблицы (create_all), потом их
# содержимое (seed справочника имён), потом представление поверх них (CREATE VIEW) —
# представление ссылается на обе таблицы и не создастся, если их ещё нет.
# Вызывается один раз при старте приложения (из lifespan).


def init_db() -> None:
    from hackneft_platform.advisor import schema  # noqa: F401
    from hackneft_platform.models import SensorData, SensorName

    _ = SensorData, SensorName
    Base.metadata.create_all(bind=engine)
    _seed_sensor_names()
    _create_sensor_query_view()
    _create_advisor_indexes()


def _seed_sensor_names() -> None:
    """Заполняет sensor_names исходными кодами датчиков и их синонимами.

    Каждый код записывается именем самого себя (sensor_code == name), после чего
    добавляются синонимы из catalog.SENSOR_SYNONYMS. Синонимы, введённые пользователем
    через интерфейс справочника, этой процедурой не затрагиваются: она касается только
    имён, перечисленных в каталоге.

    Имя указывает ровно на один датчик, поэтому перед вставкой снимается его прежняя
    привязка к другому коду. Без этого перенос ряда на новый код (например, показания
    серы стали приходить от агрегатора под кодом ht_q21 вместо Q21) оставил бы в базе,
    созданной прежней версией, две строки с одним именем, и выбор ряда зависел бы от
    порядка строк — см. проверку в api.py:create_sensor_name.

    INSERT OR IGNORE — операция идемпотентна: при повторном запуске уже вставленные
    пары молча пропускаются, ошибок из-за первичного ключа (sensor_code, name) нет.
    """
    from hackneft_platform.catalog import KNOWN_SENSOR_CODES, SENSOR_SYNONYMS

    pairs = [(code, code) for code in KNOWN_SENSOR_CODES]
    pairs += [(code, synonym) for code, synonyms in SENSOR_SYNONYMS.items() for synonym in synonyms]

    with engine.begin() as connection:
        for code, name in pairs:
            connection.execute(
                text("DELETE FROM sensor_names WHERE name = :name AND sensor_code <> :code"),
                {"code": code, "name": name},
            )
            connection.execute(
                text(
                    "INSERT OR IGNORE INTO sensor_names (sensor_code, name) VALUES (:code, :name)"
                ),
                {"code": code, "name": name},
            )


def _create_sensor_query_view() -> None:
    """Создаёт представление sensor_query, если его ещё нет.

    Не через Base.metadata.create_all (это не таблица, а команда SQL), а отдельным
    запросом здесь, при каждой инициализации БД — IF NOT EXISTS делает это безопасным
    при повторном запуске.
    """
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE VIEW IF NOT EXISTS sensor_query AS
                SELECT d.timestamp, d.sensor_code, n.name AS sensor_name, d.value
                FROM sensor_data d
                JOIN sensor_names n ON n.sensor_code = d.sensor_code
                """
            )
        )


def _create_advisor_indexes() -> None:
    """Индексы времени журнала советника в базе, созданной до их появления.

    create_all не изменяет существующие таблицы, поэтому индексы, объявленные в
    advisor/schema.py позже самих таблиц, добавляются здесь. Имена совпадают с теми,
    что create_all даёт новой базе, и IF NOT EXISTS не создаёт их повторно.
    """
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_advisor_events_occurred_at "
                "ON advisor_events (occurred_at)"
            )
        )
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_advisor_runs_requested_at "
                "ON advisor_runs (requested_at)"
            )
        )
