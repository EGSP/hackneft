"""Конфигурация агрегатора.

Значения читаются один раз при запуске. Два из них — интервал опроса и шаг курсора —
меняются во время работы через веб-интерфейс, поэтому конфигурация задаёт только их
начальные значения, а текущие хранит исполнитель (`runner.SimulationRunner`).
"""

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

SERVICE_DIR = Path(__file__).resolve().parents[2]
"""Каталог сервиса: `services/aggregator`. Относительные пути конфигурации отсчитываются от него."""


class ConfigError(Exception):
    """Ошибка конфигурации. Несёт перечень проблем целиком, а не первую из них."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("\n".join(problems))
        self.problems = tuple(problems)


@dataclass(frozen=True, slots=True)
class AggregatorConfig:
    host: str
    port: int
    source_dir: Path
    """Каталог с файлами телеметрии. Пакет данных лежит вне репозитория, поэтому путь задаётся
    настройкой, а в контейнер каталог пробрасывается как том только для чтения."""
    start_date: datetime
    """Начальное положение курсора. Используется при первом запуске и при сбросе курсора;
    при последующих запусках курсор читается из сохранённого состояния."""
    interval_minutes: float
    """Интервал опроса: реальное время между тактами отправки."""
    step_minutes: float
    """Шаг курсора: модельное время, на которое курсор продвигается за один такт.

    Отношение шага к интервалу и есть коэффициент ускорения симуляции: при равенстве
    значений поток идёт в реальном темпе, при шаге больше интервала — быстрее реального.
    """
    batch_limit: int
    """Предельное число записей в одном запросе к платформе. Такт, давший больше записей,
    отправляется несколькими запросами подряд."""
    platform_url: str
    request_timeout_s: float
    state_path: Path
    """Файл с сохранённым положением курсора."""


def load_env_file() -> None:
    """Читает `services/aggregator/.env`.

    Переменные окружения процесса имеют приоритет над значениями файла.
    """
    load_dotenv(SERVICE_DIR / ".env", override=False)


def _path(name: str, default: str) -> Path:
    path = Path(os.getenv(name) or default)
    return path if path.is_absolute() else SERVICE_DIR / path


def _number(name: str, default: float, problems: list[str], minimum: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = float(raw.replace(",", "."))
    except ValueError:
        problems.append(f"{name}: ожидается число, получено {raw!r}")
        return default
    if value < minimum:
        problems.append(f"{name}: значение {value} меньше допустимого {minimum}")
        return default
    return value


def load_config() -> AggregatorConfig:
    problems: list[str] = []

    raw_start = os.getenv("AGGREGATOR_START_DATE") or "2023-01-01T00:00:00"
    try:
        # Пробел вместо «T» допускается: в таком виде дата записана в самих файлах телеметрии,
        # и переносить её в настройку без правки удобнее, чем переписывать разделитель.
        start_date = datetime.fromisoformat(raw_start.strip().replace(" ", "T"))
    except ValueError:
        problems.append(
            "AGGREGATOR_START_DATE: ожидается дата вида 2023-01-01T00:00:00, "
            f"получено {raw_start!r}"
        )
        start_date = datetime(2023, 1, 1)

    interval_minutes = _number("AGGREGATOR_INTERVAL_MINUTES", 10.0, problems, 0.01)
    step_minutes = _number("AGGREGATOR_STEP_MINUTES", 10.0, problems, 0.01)
    batch_limit = int(_number("AGGREGATOR_BATCH_LIMIT", 500, problems, 1))
    timeout_ms = _number("AGGREGATOR_REQUEST_TIMEOUT_MS", 10000, problems, 100)
    port = int(_number("AGGREGATOR_PORT", 8200, problems, 1))

    if problems:
        raise ConfigError(problems)

    return AggregatorConfig(
        host=os.getenv("AGGREGATOR_HOST") or "127.0.0.1",
        port=port,
        source_dir=_path("AGGREGATOR_SOURCE_DIR", "data"),
        start_date=start_date,
        interval_minutes=interval_minutes,
        step_minutes=step_minutes,
        batch_limit=batch_limit,
        platform_url=(os.getenv("PLATFORM_URL") or "http://localhost:8000").rstrip("/"),
        request_timeout_s=timeout_ms / 1000,
        state_path=_path("AGGREGATOR_STATE_PATH", "data/cursor.json"),
    )
