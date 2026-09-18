"""Доступ к базе данных SQLite.

SQLite допускает одного пишущего. Параллельные транзакции записи из разных соединений
приводят к отказу «database is locked», а транзакция, начатая чтением и продолженная записью,
отказывает сразу, если за это время другое соединение успело записать. Поэтому записи
сериализуются блокировкой внутри процесса, а чтение идёт без неё: в режиме WAL читающие не
мешают пишущему. Отсюда же требование: сервис работает одним процессом.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .schema import Base


class Database:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._engine = create_async_engine(f"sqlite+aiosqlite:///{path.as_posix()}")
        event.listen(self._engine.sync_engine, "connect", _configure_connection)
        self._sessions = async_sessionmaker(self._engine, expire_on_commit=False)
        self._write_lock = asyncio.Lock()

    async def create_schema(self) -> None:
        async with self._engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    @asynccontextmanager
    async def read(self) -> AsyncIterator[AsyncSession]:
        async with self._sessions() as session:
            yield session

    @asynccontextmanager
    async def write(self) -> AsyncIterator[AsyncSession]:
        """Транзакция записи. Фиксируется при выходе без исключения.

        Вкладывать одну запись в другую нельзя: блокировка не повторно входимая.
        """
        async with self._write_lock, self._sessions() as session, session.begin():
            yield session

    async def close(self) -> None:
        await self._engine.dispose()


def _configure_connection(connection: Any, _record: Any) -> None:
    """Настройки соединения: внешние ключи SQLite по умолчанию не проверяет."""
    cursor = connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()
