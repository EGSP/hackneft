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


def ai_service_url() -> str:
    """Адрес ИИ-сервиса из `AI_SERVICE_URL`, по умолчанию `http://localhost:8100`.

    Читается при каждом обращении страницы «Сессии» веб-интерфейса: платформа передаёт
    ИИ-сервису её запросы чтения сессий (`ai_sessions.py`). Ядро платформы от этой
    настройки не зависит: без ИИ-сервиса приём и выдача показаний работают, а страница
    сессий сообщает о его недоступности.
    """
    return (os.getenv("AI_SERVICE_URL") or "http://localhost:8100").rstrip("/")
