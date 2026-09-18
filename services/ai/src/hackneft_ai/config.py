"""Конфигурация сервиса.

Конфигурация проверяется при запуске, и сервис не стартует с неполной: обнаружить ошибку в
настройке на первом же запросе хуже, чем при запуске.

Ни провайдеров, ни моделей в конфигурации нет: их источники — справочник провайдеров и
справочник моделей. Справочники меняются во время работы и могут опустеть уже после запуска,
поэтому их проверяет не запуск, а начало хода.
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

SERVICE_DIR = Path(__file__).resolve().parents[2]
"""Каталог сервиса: `services/ai`. Относительные пути конфигурации отсчитываются от него."""


class ConfigError(Exception):
    """Ошибка конфигурации. Несёт перечень проблем целиком, а не первую из них."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("\n".join(problems))
        self.problems = tuple(problems)


@dataclass(frozen=True, slots=True)
class AgentConfig:
    max_steps: int
    temperature: float
    max_tokens: int | None
    tool_result_max_chars: int


@dataclass(frozen=True, slots=True)
class McpConfig:
    """Обращения к внешним серверам MCP.

    Пределов времени два, а не один: общий предел оставил бы зависший сервер удерживать ход
    агента до его отмены.
    """

    connect_timeout_s: float
    """Предел на подключение, инициализацию и запрос перечня инструментов."""
    call_timeout_s: float
    """Предел на один вызов инструмента."""
    warn_tool_count: int
    """Порог предупреждения о числе инструментов в наборе. Набор при этом не сокращается."""
    warn_schema_chars: int
    """Порог предупреждения об объёме схем инструментов."""


@dataclass(frozen=True, slots=True)
class TracingConfig:
    enabled: bool
    endpoint: str
    service_name: str
    capture_content: bool
    """Записывать ли в спаны тексты запросов и ответов модели. Без них вид переписки в
    приёмнике не собирается."""


@dataclass(frozen=True, slots=True)
class AppConfig:
    host: str
    port: int
    cors_origins: tuple[str, ...]
    database_path: Path
    agent: AgentConfig
    mcp: McpConfig
    tracing: TracingConfig


def load_env_file() -> None:
    """Читает `services/ai/.env`. Переменные окружения процесса имеют приоритет над файлом."""
    load_dotenv(SERVICE_DIR / ".env", override=False)


def load_config(env: Mapping[str, str] | None = None) -> AppConfig:
    source = os.environ if env is None else env
    reader = _Reader(source)

    config = AppConfig(
        host=reader.text("AI_HOST") or "127.0.0.1",
        port=reader.integer("AI_PORT", 8100, minimum=1),
        cors_origins=tuple(
            origin.strip()
            for origin in (reader.text("AI_CORS_ORIGIN") or "*").split(",")
            if origin.strip() != ""
        ),
        database_path=_resolve(reader.text("AI_DATABASE_PATH") or "data/ai.db"),
        agent=AgentConfig(
            max_steps=reader.integer("AGENT_MAX_STEPS", 10, minimum=1),
            temperature=reader.number("AGENT_TEMPERATURE", 0.3),
            max_tokens=reader.optional_integer("AGENT_MAX_TOKENS"),
            tool_result_max_chars=reader.integer("TOOL_RESULT_MAX_CHARS", 8000, minimum=100),
        ),
        mcp=McpConfig(
            connect_timeout_s=reader.integer("MCP_CONNECT_TIMEOUT_MS", 30_000, minimum=1) / 1000,
            call_timeout_s=reader.integer("MCP_CALL_TIMEOUT_MS", 60_000, minimum=1) / 1000,
            warn_tool_count=reader.integer("MCP_WARN_TOOL_COUNT", 60, minimum=1),
            warn_schema_chars=reader.integer("MCP_WARN_SCHEMA_CHARS", 60_000, minimum=1),
        ),
        tracing=_tracing(reader),
    )
    if reader.problems:
        raise ConfigError(reader.problems)
    return config


def _tracing(reader: "_Reader") -> TracingConfig:
    endpoint = reader.text("OTEL_EXPORTER_OTLP_ENDPOINT").rstrip("/")
    capture = reader.text("OTEL_CAPTURE_CONTENT").lower()
    return TracingConfig(
        enabled=endpoint != "",
        endpoint=endpoint,
        service_name=reader.text("OTEL_SERVICE_NAME") or "hackneft-ai",
        capture_content=capture not in ("false", "0"),
    )


def _resolve(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else SERVICE_DIR / path


class _Reader:
    """Чтение переменных с накоплением проблем, а не отказом на первой."""

    def __init__(self, env: Mapping[str, str]) -> None:
        self._env = env
        self.problems: list[str] = []

    def text(self, name: str) -> str:
        return self._env.get(name, "").strip()

    def integer(self, name: str, default: int, *, minimum: int | None = None) -> int:
        raw = self.text(name)
        if raw == "":
            return default
        try:
            value = int(raw)
        except ValueError:
            self.problems.append(f"{name}: ожидается целое число, получено «{raw}»")
            return default
        if minimum is not None and value < minimum:
            self.problems.append(f"{name}: значение должно быть не меньше {minimum}")
            return default
        return value

    def optional_integer(self, name: str) -> int | None:
        return None if self.text(name) == "" else self.integer(name, 0, minimum=1)

    def number(self, name: str, default: float) -> float:
        raw = self.text(name)
        if raw == "":
            return default
        try:
            return float(raw)
        except ValueError:
            self.problems.append(f"{name}: ожидается число, получено «{raw}»")
            return default
