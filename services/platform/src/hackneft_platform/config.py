"""Конфигурация сервиса."""

import os
from pathlib import Path

from dotenv import load_dotenv

SERVICE_DIR = Path(__file__).resolve().parents[2]
"""Каталог сервиса: `services/platform`. Относительные пути конфигурации отсчитываются от него."""


def load_env_file() -> None:
    """Читает `services/platform/.env`. Переменные окружения процесса имеют приоритет над файлом."""
    load_dotenv(SERVICE_DIR / ".env", override=False)


def database_path() -> Path:
    """Файл базы SQLite из `PLATFORM_DATABASE_PATH`, по умолчанию `data/platform.db`."""
    path = Path(os.getenv("PLATFORM_DATABASE_PATH") or "data/platform.db")
    return path if path.is_absolute() else SERVICE_DIR / path
