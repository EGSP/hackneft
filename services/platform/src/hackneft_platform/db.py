from collections.abc import Generator

from sqlalchemy import create_engine
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

# Инициализация схемы БД: создаёт все таблицы, описанные в моделях.
# Вызывается один раз при старте приложения (из lifespan).


def init_db() -> None:
    from hackneft_platform.models import SensorData

    _ = SensorData
    Base.metadata.create_all(bind=engine)
