"""Начальное заполнение справочников при запуске.

Сервис поднимается в compose без ручной настройки: провайдер Yandex заводится по файлу секретов
из каталога `AI_BOOTSTRAP_DIR`, вместе с ним — модель по умолчанию, а подключения MCP — по
конфигурации в формате MCP-клиентов. Заполняется только отсутствующее: записи, заведённые или
изменённые через API, остаются как есть.

Подключения MCP заводятся в фоне с повторами. Справочник не принимает недостижимый сервер, а
сервер MCP может подняться позже ИИ-сервиса — в compose платформа запускается после него.
"""

import asyncio
import logging
from pathlib import Path

from dotenv import dotenv_values

from hackneft_common.ai import (
    CreateMcpConnectionRequest,
    CreateModelProfileRequest,
    CreateProviderRequest,
)

from .config import BootstrapConfig
from .errors import ServiceError
from .mcp.config_file import McpConfigError, McpImportEntry, parse_mcp_servers_file
from .mcp.directory import McpDirectory
from .models.service import ModelDirectory
from .providers.directory import ProviderDirectory

logger = logging.getLogger(__name__)

YANDEX_PROVIDER = "yandex"
YANDEX_SECRETS_FILE = "yandex.env"
MCP_CONFIG_FILE = "mcp.json"

_YANDEX_KEYS = (
    "YANDEX_FOLDER_ID",
    "YANDEX_BASE_URL",
    "YANDEX_IAM_TOKEN",
    "YANDEX_KEY_ID",
    "YANDEX_SERVICE_ACCOUNT_ID",
    "YANDEX_PRIVATE_KEY",
)
"""Ключи, переносимые из файла секретов. Остальные строки файла (например, env-файл xip
целиком) пропускаются: провайдер отвергает незнакомые ключи."""

_MCP_RETRY_PERIOD_S = 5.0
_MCP_RETRY_LIMIT_S = 600.0


async def bootstrap_directories(
    config: BootstrapConfig,
    providers: ProviderDirectory,
    models: ModelDirectory,
) -> None:
    """Заводит провайдера Yandex и модель по умолчанию. Отказ записывается в журнал, запуск
    сервиса он не прерывает: справочники можно заполнить и через API."""
    if config.directory is None:
        return
    secrets = _read_yandex_secrets(config.directory / YANDEX_SECRETS_FILE)
    if secrets is None:
        return

    existing = {provider.name for provider in await providers.list_providers()}
    if YANDEX_PROVIDER not in existing:
        try:
            provider = await providers.create(
                CreateProviderRequest(
                    name=YANDEX_PROVIDER, title="Yandex AI Studio", type="yandex", secrets=secrets
                )
            )
        except ServiceError as error:
            logger.warning("провайдер %s не заведён: %s", YANDEX_PROVIDER, error.message)
            return
        logger.info(
            "провайдер %s заведён по %s, проверка: %s",
            YANDEX_PROVIDER,
            YANDEX_SECRETS_FILE,
            provider.check_status,
        )

    profiles = await models.list_profiles()
    if any(
        profile.provider == YANDEX_PROVIDER and profile.identifier == config.default_model
        for profile in profiles
    ):
        return
    try:
        await models.create(
            CreateModelProfileRequest(
                provider=YANDEX_PROVIDER,
                identifier=config.default_model,
                # Моделью по умолчанию запись становится, только если другой ещё нет:
                # выбор, сделанный через API, не отменяется.
                is_default=not any(profile.is_default for profile in profiles),
                supports_tools=True,
                supports_reasoning=True,
            )
        )
    except ServiceError as error:
        logger.warning("модель %s не заведена: %s", config.default_model, error.message)
        return
    logger.info("модель %s заведена", config.default_model)


def start_mcp_bootstrap(config: BootstrapConfig, directory: McpDirectory) -> asyncio.Task[None]:
    """Запускает фоновое заведение подключений MCP. Задачу отменяет остановка сервиса."""
    return asyncio.create_task(_bootstrap_mcp(config, directory), name="bootstrap-mcp")


async def _bootstrap_mcp(config: BootstrapConfig, directory: McpDirectory) -> None:
    entries = _mcp_entries(config)
    if not entries:
        return
    existing = {connection.name for connection in await directory.list_connections()}
    pending = [entry for entry in entries if entry.name not in existing]

    waited = 0.0
    last_error = ""
    while pending:
        remaining = []
        for entry in pending:
            try:
                await directory.create(
                    CreateMcpConnectionRequest(
                        name=entry.name,
                        title=None if entry.source_key == entry.name else entry.source_key,
                        transport=entry.transport,
                    )
                )
                logger.info("подключение MCP %s заведено", entry.name)
            except ServiceError as error:
                remaining.append(entry)
                last_error = error.message
        pending = remaining
        if not pending:
            return
        if waited >= _MCP_RETRY_LIMIT_S:
            logger.warning(
                "подключения MCP не заведены: %s. Последняя причина: %s",
                ", ".join(entry.name for entry in pending),
                last_error,
            )
            return
        await asyncio.sleep(_MCP_RETRY_PERIOD_S)
        waited += _MCP_RETRY_PERIOD_S


def _mcp_entries(config: BootstrapConfig) -> list[McpImportEntry]:
    texts: list[tuple[str, str]] = []
    if config.directory is not None:
        path = config.directory / MCP_CONFIG_FILE
        if path.is_file():
            texts.append((str(path), path.read_text(encoding="utf-8")))
    if config.mcp_config:
        texts.append(("AI_BOOTSTRAP_MCP", config.mcp_config))

    entries: dict[str, McpImportEntry] = {}
    for origin, text in texts:
        try:
            for entry in parse_mcp_servers_file(text):
                entries[entry.name] = entry
        except McpConfigError as error:
            logger.warning("конфигурация MCP из %s не разобрана: %s", origin, error)
    return list(entries.values())


def _read_yandex_secrets(path: Path) -> dict[str, str] | None:
    if not path.is_file():
        logger.info("файл секретов %s не найден, провайдер Yandex не заводится", path)
        return None
    values = dotenv_values(path)
    secrets = {
        key: value.strip()
        for key in _YANDEX_KEYS
        if (value := values.get(key)) is not None and value.strip() != ""
    }
    if not secrets:
        logger.warning("в файле %s нет ключей YANDEX_*", path)
        return None
    return secrets
