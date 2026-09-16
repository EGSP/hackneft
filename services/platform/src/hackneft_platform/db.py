import os
from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


database_url = os.getenv("DATABASE_URL", "sqlite:///./hackneft.db")
engine = create_engine(
    database_url,
    connect_args={"check_same_thread": False} if database_url.startswith(
        "sqlite") else {},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db() -> Generator[Session, None, None]:
    with SessionLocal() as session:
        yield session


def init_db() -> None:
    from hackneft_platform.models import DataSource, Measurement

    _ = (DataSource, Measurement)
    Base.metadata.create_all(bind=engine)
