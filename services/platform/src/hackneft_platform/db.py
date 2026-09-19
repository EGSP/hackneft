from collections.abc import Generator

from sqlalchemy import create_engine, text
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
# Для SQLite отключаем проверку "check_same_thread", чтобы соединение можно было использовать из разных потоков (нужно для FastAPI).
# Для остальных СУБД дополнительные аргументы не требуются.
engine = create_engine(
    database_url,
    connect_args={"check_same_thread": False} if database_url.startswith(
        "sqlite") else {},
)

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
    from hackneft_platform.models import SensorData, SensorName

    _ = SensorData, SensorName
    Base.metadata.create_all(bind=engine)
    _seed_sensor_names()
    _create_sensor_query_view()


def _seed_sensor_names() -> None:
    """Заполняет sensor_names исходными кодами датчиков (код как имя самого себя).

    INSERT OR IGNORE — операция идемпотентна: при повторном запуске уже вставленные
    коды молча пропускаются, ошибок из-за первичного ключа (sensor_code, name) нет.
    """
    from hackneft_platform.catalog import KNOWN_SENSOR_CODES

    with engine.begin() as connection:
        for code in KNOWN_SENSOR_CODES:
            connection.execute(
                text(
                    "INSERT OR IGNORE INTO sensor_names (sensor_code, name) "
                    "VALUES (:code, :code)"
                ),
                {"code": code},
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
