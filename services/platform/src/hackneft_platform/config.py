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

    Используется работником advisor/runtime.py после единого допуска запуска и читается
    при каждом обращении страницы «Сессии» веб-интерфейса: платформа передаёт ИИ-сервису
    её запросы чтения сессий (`ai_sessions.py`). Без ИИ-сервиса приём и выдача показаний
    работают, а страница сессий сообщает о его недоступности.
    """
    return (os.getenv("AI_SERVICE_URL") or "http://localhost:8100").rstrip("/")


def aggregator_url() -> str:
    """Адрес агрегатора из `AGGREGATOR_URL`, по умолчанию `http://localhost:8200`.

    Работник советника включает у агрегатора темп реального времени на время работы советника
    и снимает его по завершении. Без агрегатора советник работает, а флаг не ставится.
    """
    return (os.getenv("AGGREGATOR_URL") or "http://localhost:8200").rstrip("/")
