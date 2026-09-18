"""Периодическая проверка доступности моделей справочника.

Доступность определяется наличием модели в перечне провайдера, а не пробным обращением:
обращение расходует токены и время, тогда как ответ на вопрос «существует ли модель» даёт
перечень.

Три исхода различаются намеренно. Модель есть в перечне — доступна. Перечень получен, но
модели в нём нет — неверна запись в справочнике либо модель отключена. Перечень получить не
удалось — неисправно окружение, и о самой модели ничего не известно.
"""

import asyncio
import contextlib
import logging
from collections import defaultdict

from sqlalchemy import select, update

from ..db.database import Database
from ..db.schema import LlmModelRow, utc_now
from .base import ProviderModelsFailed
from .registry import ProviderRegistry

INTERVAL_S = 60

logger = logging.getLogger(__name__)


class ModelAvailabilityChecker:
    def __init__(self, db: Database, providers: ProviderRegistry) -> None:
        self._db = db
        self._providers = providers
        self._task: asyncio.Task[None] | None = None
        self._running = False
        """Не допускает наложения проверок, если очередная затянулась дольше интервала."""

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="model-availability")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _loop(self) -> None:
        while True:
            await self.refresh()
            await asyncio.sleep(INTERVAL_S)

    async def refresh(self, *, force: bool = False) -> int:
        """Обновляет доступность всех записей справочника. Возвращает число обновлённых."""
        if self._running:
            return 0
        self._running = True
        try:
            return await self._refresh(force)
        except Exception as error:
            logger.warning("проверка доступности моделей не выполнена: %s", error)
            return 0
        finally:
            self._running = False

    async def _refresh(self, force: bool) -> int:
        async with self._db.read() as session:
            rows = (
                await session.execute(
                    select(LlmModelRow.id, LlmModelRow.provider, LlmModelRow.identifier)
                )
            ).all()
        if not rows:
            return 0

        by_provider: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for model_id, provider_name, identifier in rows:
            by_provider[provider_name].append((model_id, identifier))

        verdicts: list[tuple[str, str, str]] = []
        for provider_name, models in by_provider.items():
            provider = self._providers.get(provider_name)
            if provider is None:
                message = f"Провайдера «{provider_name}» нет в справочнике провайдеров."
                verdicts += [(model_id, "unreachable", message) for model_id, _ in models]
                continue

            listing = await provider.list_models(force=force)
            if isinstance(listing, ProviderModelsFailed):
                # Прежнее суждение о модели не заменяется на «отсутствует»: оснований для
                # такого вывода нет, неисправно окружение.
                verdicts += [(model_id, "unreachable", listing.error) for model_id, _ in models]
                continue

            known = {name for model in listing.models for name in (model.identifier, model.full_id)}
            for model_id, identifier in models:
                present = identifier in known
                verdicts.append(
                    (
                        model_id,
                        "available" if present else "not_listed",
                        f"Модель присутствует в перечне провайдера ({len(listing.models)} моделей)."
                        if present
                        else "Модели нет в перечне провайдера. Проверьте идентификатор.",
                    )
                )

        now = utc_now()
        async with self._db.write() as tx:
            for model_id, availability, message in verdicts:
                await tx.execute(
                    update(LlmModelRow)
                    .where(LlmModelRow.id == model_id)
                    .values(
                        availability=availability, last_check_at=now, last_check_message=message
                    )
                )
        return len(verdicts)
