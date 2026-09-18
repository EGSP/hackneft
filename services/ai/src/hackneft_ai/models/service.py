"""Справочник моделей — единственный источник модели хода.

Справочник один на весь сервис, как и пометка модели по умолчанию. В конфигурации модель не
задаётся: пока справочник пуст, ход агента не запускается, и отказ получает то действие,
которое ход запускает. Непустой справочник всегда содержит модель по умолчанию: первая запись
получает пометку сама, а снять её можно только назначением другой записи.

Провайдер и идентификатор записи задаются при создании и не меняются. Поэтому запись и модель
соответствуют друг другу однозначно, а сессия, закреплённая за записью, не может перейти на
другую модель без явного выбора.
"""

from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import func, select, update

from hackneft_common.ai import (
    CreateModelProfileRequest,
    ModelAvailability,
    ModelInUseError,
    ModelProfile,
    ModelSession,
    SessionKind,
    UpdateModelProfileRequest,
)

from ..db.database import Database
from ..db.schema import LlmModelRow, SessionRow, iso
from ..errors import ConflictError, NotFoundError
from ..providers.availability import ModelAvailabilityChecker
from ..providers.registry import ProviderRegistry

_NO_MODELS = "В справочнике нет ни одной модели. Добавьте модель запросом POST /api/models."


@dataclass(frozen=True, slots=True)
class ModelChoice:
    """Модель, выбранная для сессии или хода: запись справочника и модель у провайдера."""

    id: str
    provider: str
    identifier: str


class ModelDirectory:
    def __init__(
        self, db: Database, providers: ProviderRegistry, availability: ModelAvailabilityChecker
    ) -> None:
        self._db = db
        self._providers = providers
        self._availability = availability

    async def list_profiles(self) -> list[ModelProfile]:
        async with self._db.read() as session:
            rows = (
                await session.scalars(
                    select(LlmModelRow).order_by(
                        LlmModelRow.is_default.desc(), LlmModelRow.provider, LlmModelRow.identifier
                    )
                )
            ).all()
        active = await self._active_sessions()
        return [_to_profile(row, active.get(row.id, [])) for row in rows]

    async def create(self, request: CreateModelProfileRequest) -> ModelProfile:
        self._providers.require(request.provider)
        identifier = request.identifier.strip()

        async with self._db.write() as tx:
            existing = await tx.scalar(
                select(LlmModelRow.id).where(
                    LlmModelRow.provider == request.provider,
                    LlmModelRow.identifier == identifier,
                )
            )
            if existing is not None:
                raise ConflictError(
                    f'Модель "{identifier}" провайдера {request.provider} уже добавлена'
                )
            count = await tx.scalar(select(func.count()).select_from(LlmModelRow)) or 0
            # Первая добавленная модель становится используемой по умолчанию: иначе сервис
            # остался бы без модели, пока пометку не проставят вручную.
            is_default = request.is_default is True or count == 0
            if is_default:
                await tx.execute(
                    update(LlmModelRow).where(LlmModelRow.is_default).values(is_default=False)
                )
            row = LlmModelRow(
                provider=request.provider,
                identifier=identifier,
                is_default=is_default,
                supports_tools=request.supports_tools or False,
                supports_reasoning=request.supports_reasoning or False,
            )
            tx.add(row)
            await tx.flush()
            created_id = row.id

        # Сессии, чья запись с той же моделью была удалена, снова получают модель.
        await self.relink(request.provider, identifier)
        # Доступность выясняется сразу: запись не должна оставаться непроверенной до таймера.
        await self._availability.refresh()
        return await self.require(created_id)

    async def update(self, model_id: str, request: UpdateModelProfileRequest) -> ModelProfile:
        """Правка признаков и пометки по умолчанию.

        Снять пометку по умолчанию можно только назначением другой записи: иначе справочник с
        записями остался бы без модели по умолчанию, и новый чат не получил бы модели.
        """
        current = await self.require(model_id)
        if request.is_default is False and current.is_default:
            raise ConflictError(
                "Нельзя снять пометку с модели по умолчанию. Назначьте моделью по умолчанию другую."
            )

        values: dict[str, bool] = {}
        if request.is_default is not None:
            values["is_default"] = request.is_default
        if request.supports_tools is not None:
            values["supports_tools"] = request.supports_tools
        if request.supports_reasoning is not None:
            values["supports_reasoning"] = request.supports_reasoning

        async with self._db.write() as tx:
            if request.is_default is True:
                await tx.execute(
                    update(LlmModelRow).where(LlmModelRow.is_default).values(is_default=False)
                )
            if values:
                await tx.execute(
                    update(LlmModelRow).where(LlmModelRow.id == model_id).values(**values)
                )
        return await self.require(model_id)

    async def remove(self, model_id: str) -> None:
        """Удаление записи.

        Пока моделью исполняется ход, запись не удаляется, и отказ перечисляет эти сессии.
        После удаления сессии, закреплённые за записью, сохраняются вместе с историей, но ход
        в них отклоняется, пока не выбрана другая модель.
        """
        model = await self.require(model_id)
        if model.active_sessions:
            titles = ", ".join(f"«{session.title}»" for session in model.active_sessions)
            message = (
                f"Модель используется сессиями, в которых идёт ход: {titles}. "
                "Дождитесь завершения ходов или прервите их."
            )
            body = ModelInUseError(
                error="Conflict", message=message, sessions=model.active_sessions
            )
            raise ConflictError(
                message, extra={"sessions": body.model_dump(mode="json")["sessions"]}
            )

        async with self._db.write() as tx:
            count = await tx.scalar(select(func.count()).select_from(LlmModelRow)) or 0
            if model.is_default and count > 1:
                raise ConflictError("Нельзя удалить модель по умолчанию. Сначала назначьте другую.")
            row = await tx.get(LlmModelRow, model_id)
            if row is not None:
                await tx.delete(row)

    async def require(self, model_id: str) -> ModelProfile:
        async with self._db.read() as session:
            row = await session.get(LlmModelRow, model_id)
        if row is None:
            raise NotFoundError(f"Модель {model_id} не найдена")
        active = await self._active_sessions(model_id)
        return _to_profile(row, active.get(model_id, []))

    async def default_model(self) -> ModelChoice | None:
        """Модель по умолчанию. Пусто, если справочник пуст."""
        async with self._db.read() as session:
            row = await session.scalar(select(LlmModelRow).where(LlmModelRow.is_default))
        return None if row is None else ModelChoice(row.id, row.provider, row.identifier)

    async def require_default(self) -> ModelChoice:
        model = await self.default_model()
        if model is None:
            raise ConflictError(_NO_MODELS)
        return model

    async def choice(self, model_id: str) -> ModelChoice:
        profile = await self.require(model_id)
        return ModelChoice(profile.id, profile.provider, profile.identifier)

    async def for_turn(
        self, model_id: str | None, provider: str | None, identifier: str | None
    ) -> ModelChoice:
        """Модель хода.

        Модель, назначенная сессии, берётся из её записи. Если связи с записью нет, модель
        ищется по запомненному провайдеру и идентификатору: запись могла быть удалена и
        заведена заново. Если модели сессии в справочнике нет, ход отклоняется: подставлять
        другую модель без ведома пользователя нельзя. Модель по умолчанию достаётся только
        сессии, которой модель ещё не назначалась.
        """
        if model_id is None and identifier is None:
            return await self.require_default()

        async with self._db.read() as session:
            row = None if model_id is None else await session.get(LlmModelRow, model_id)
            if row is None and provider is not None and identifier is not None:
                row = await session.scalar(
                    select(LlmModelRow).where(
                        LlmModelRow.provider == provider, LlmModelRow.identifier == identifier
                    )
                )
        if row is not None:
            return ModelChoice(row.id, row.provider, row.identifier)

        raise ConflictError(
            "Модель сессии удалена из справочника. Выберите для сессии другую модель."
            if identifier is None
            else f'Модели "{identifier}" провайдера {provider} нет в справочнике. '
            "Выберите для сессии другую модель."
        )

    async def relink(self, provider: str | None = None, identifier: str | None = None) -> None:
        """Восстанавливает связь сессий с записью по провайдеру и идентификатору модели.

        Связи нет у сессий, чья запись удалена. Провайдер и идентификатор записи неизменяемы,
        поэтому их совпадение означает ту же модель, и восстановление связи не подменяет её.
        """
        query = select(LlmModelRow.id, LlmModelRow.provider, LlmModelRow.identifier)
        if provider is not None and identifier is not None:
            query = query.where(
                LlmModelRow.provider == provider, LlmModelRow.identifier == identifier
            )
        async with self._db.read() as session:
            models = (await session.execute(query)).all()
        if not models:
            return
        async with self._db.write() as tx:
            for model_id, model_provider, model_identifier in models:
                await tx.execute(
                    update(SessionRow)
                    .where(
                        SessionRow.model_id.is_(None),
                        SessionRow.model_provider == model_provider,
                        SessionRow.model_identifier == model_identifier,
                    )
                    .values(model_id=model_id)
                )

    async def _active_sessions(self, model_id: str | None = None) -> dict[str, list[ModelSession]]:
        """Сессии с идущим ходом по записям справочника."""
        query = (
            select(SessionRow.id, SessionRow.title, SessionRow.kind, SessionRow.model_id)
            .where(SessionRow.status == "running", SessionRow.model_id.is_not(None))
            .order_by(SessionRow.last_event_at.desc())
        )
        if model_id is not None:
            query = query.where(SessionRow.model_id == model_id)
        async with self._db.read() as session:
            rows = (await session.execute(query)).all()
        by_model: dict[str, list[ModelSession]] = defaultdict(list)
        for session_id, title, kind, owner_model in rows:
            if owner_model is not None:
                by_model[owner_model].append(
                    ModelSession(id=session_id, title=title, kind=_kind(kind))
                )
        return by_model


def _kind(value: str) -> SessionKind:
    return "agent" if value == "agent" else "chat"


_AVAILABILITY: dict[str, ModelAvailability] = {
    "available": "available",
    "not_listed": "not_listed",
    "unreachable": "unreachable",
}


def _to_profile(row: LlmModelRow, active: list[ModelSession]) -> ModelProfile:
    return ModelProfile(
        id=row.id,
        provider=row.provider,
        identifier=row.identifier,
        is_default=row.is_default,
        supports_tools=row.supports_tools,
        supports_reasoning=row.supports_reasoning,
        availability=_AVAILABILITY.get(row.availability, "unknown"),
        last_check_at=None if row.last_check_at is None else iso(row.last_check_at),
        last_check_message=row.last_check_message,
        created_at=iso(row.created_at),
        active_sessions=active,
    )
