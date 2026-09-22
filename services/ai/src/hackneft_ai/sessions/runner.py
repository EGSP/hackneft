"""Запуск и остановка ходов агента.

Ход исполняется фоновой задачей asyncio и не привязан к времени жизни HTTP-запроса: клиент
может закрыть соединение, а ход продолжится. Поток событий — это подписка на ход, а не сам ход.

Две дисциплины различаются тем, что считается завершением. В чат-сессии терминальный вызов
завершает ход, после чего сессия ожидает следующего сообщения. В агентской сессии тот же вызов
завершает сессию целиком: следующего сообщения не будет, а итог отдаётся вызывающей стороне.

Ход без модели из справочника не запускается. Модель определяет метод `prepare`, и запуск хода
принимает только её: другого пути получить модель хода нет.
"""

import asyncio
import contextlib
import logging
import time
import traceback
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from opentelemetry.trace import Span, Status, StatusCode
from sqlalchemy import select, update

from hackneft_common.ai import (
    RequestSnapshotContent,
    SessionCompletedEvent,
    SessionContextResponse,
    SessionEvent,
    SessionFailedEvent,
    StepStartedEvent,
    TurnFailedEvent,
    TurnFailureReason,
    UserMessageEvent,
)

from ..config import AgentConfig, TracingConfig
from ..core.context import ContextInput, measure_context
from ..core.errors import (
    OutputBudgetExhausted,
    StepLimitReached,
    TurnError,
    describe_turn_error,
)
from ..core.requirements import TurnDeps
from ..core.snapshot import request_snapshot, sourced_tools
from ..core.system_prompt import system_prompt
from ..core.terminal import CompletionMode
from ..core.turn import TurnOptions, TurnResult, run_turn
from ..db.database import Database
from ..db.schema import SessionEventRow, SessionRow
from ..errors import ConflictError, NotFoundError
from ..models.service import (
    ModelChoice,
    ModelDirectory,
    ModelTurnError,
    ModelUnavailableError,
)
from ..providers.base import ChatSettings
from ..providers.registry import ProviderRegistry
from ..telemetry.attributes import (
    session_attributes,
    turn_input_attributes,
    turn_output_attributes,
)
from ..telemetry.observer import TracedModelClient, TracingToolObserver
from ..telemetry.session_traces import SessionTraceRegistry
from ..telemetry.tracing import tracer
from ..tools.factory import SessionToolRegistry, ToolsFactory
from .journal import SessionJournal
from .snapshots import RequestSnapshotStore

logger = logging.getLogger(__name__)

STOPPED = "Ход прерван остановкой сервиса."
"""Отказ хода, который исполнялся в момент остановки сервиса."""
_ABORTED = "Ход прерван пользователем."
_SHUTDOWN_WAIT_S = 10


@dataclass(frozen=True, slots=True)
class _Discipline:
    completion: CompletionMode
    terminal: bool
    """Завершает ли исход хода сессию целиком."""
    tools: Sequence[str] | None = None
    traceparent: str | None = None
    result_schema: Mapping[str, Any] | None = None


@dataclass(slots=True)
class _Turn:
    """Ход, исполняющийся в этом процессе."""

    task: "asyncio.Task[TurnResult]"
    span: Span
    terminal: bool
    started_at: float
    ended_at: float | None = None
    finished: asyncio.Event = field(default_factory=asyncio.Event)
    """Исход хода записан, и сессия приняла итоговое состояние."""


class AgentRunner:
    def __init__(
        self,
        *,
        db: Database,
        journal: SessionJournal,
        snapshots: RequestSnapshotStore,
        models: ModelDirectory,
        providers: ProviderRegistry,
        tools: ToolsFactory,
        traces: SessionTraceRegistry,
        agent: AgentConfig,
        tracing: TracingConfig,
    ) -> None:
        self._db = db
        self._journal = journal
        self._snapshots = snapshots
        self._models = models
        self._providers = providers
        self._tools = tools
        self._traces = traces
        self._agent = agent
        self._capture = tracing.capture_content
        self._turns: dict[str, _Turn] = {}
        """Задачи ходов этого процесса. Нужны для прерывания: признаком идущего хода служит
        состояние `running` в записи сессии."""
        self._claimed: set[str] = set()
        """Сессии, для которых ход принят к запуску. Занимаются без ожидания, поэтому два
        сообщения, пришедшие одновременно, не запустят в сессии два хода."""
        self._finishers: set[asyncio.Task[None]] = set()
        self._stopping = False

    def claim(self, session_id: str) -> None:
        if session_id in self._claimed:
            raise ConflictError("В сессии уже выполняется ход")
        self._claimed.add(session_id)

    def release(self, session_id: str) -> None:
        self._claimed.discard(session_id)

    async def reconcile_on_startup(self) -> None:
        """Сверка при запуске.

        Задача хода существует только в памяти процесса, а состояние `running` хранится в
        записи сессии и остановку переживает. После аварийной остановки посреди хода запись
        осталась бы в этом состоянии, хотя исполнять ход некому. Сразу после запуска задач нет
        ни у одной сессии, поэтому каждая сессия в `running` получает исход прерванного хода.
        """
        async with self._db.read() as session:
            rows = (
                await session.execute(
                    select(SessionRow.id, SessionRow.kind).where(SessionRow.status == "running")
                )
            ).all()
        if not rows:
            return
        logger.warning("сессий, оставшихся в состоянии running после остановки: %d", len(rows))
        for session_id, kind in rows:
            try:
                await self._close_interrupted(session_id, terminal=kind == "agent")
            except Exception as error:
                logger.error("сверка сессии %s не выполнена: %s", session_id, error)

    async def _close_interrupted(self, session_id: str, *, terminal: bool) -> None:
        """Закрывает ход, прерванный остановкой. Закрывающее событие дописывается, только если
        последний ход в журнале не закрыт, — иначе к завершённому ходу добавился бы отказ."""
        async with self._db.read() as session:
            last = await session.scalar(
                select(SessionEventRow.type)
                .where(
                    SessionEventRow.session_id == session_id,
                    SessionEventRow.type.in_(("user_message", "turn_finished", "turn_failed")),
                )
                .order_by(SessionEventRow.seq.desc())
                .limit(1)
            )
        if last == "user_message":
            await self._journal.append(
                session_id, TurnFailedEvent(reason="internal", message=STOPPED)
            )
        if terminal:
            await self._journal.settle(session_id, SessionFailedEvent(message=STOPPED))
            return
        await self._set_status(session_id, "idle")

    async def prepare(self, session_id: str) -> ModelChoice:
        """Модель следующего хода сессии.

        Определяется до любых записей в сессии. Модель, назначенная впервые, закрепляется за
        сессией: иначе смена модели по умолчанию между ходами переводила бы сессию на другую
        модель без ведома пользователя.

        Отказ из-за модели — `ModelTurnError` — вызывающая сторона записывает в журнал методом
        `reject`. Модель считается недоступной, если её провайдера нет в справочнике либо
        перечень провайдера получен и модели в нём нет. Неудачное получение перечня ход не
        останавливает: оно говорит о состоянии окружения, а не о модели, и настоящий отказ
        провайдера придёт ответом на обращение к модели.
        """
        async with self._db.read() as session:
            row = await session.get(SessionRow, session_id)
        if row is None:
            raise NotFoundError(f"Сессия {session_id} не найдена")
        model = await self._models.for_turn(row.model_id, row.model_provider, row.model_identifier)
        if (row.model_id, row.model_provider, row.model_identifier) != (
            model.id,
            model.provider,
            model.identifier,
        ):
            async with self._db.write() as tx:
                await tx.execute(
                    update(SessionRow)
                    .where(SessionRow.id == session_id)
                    .values(
                        model_id=model.id,
                        model_provider=model.provider,
                        model_identifier=model.identifier,
                    )
                )
        if self._providers.get(model.provider) is None:
            raise ModelUnavailableError(
                f"Модель «{model.identifier}» недоступна: провайдера «{model.provider}» нет в "
                "справочнике провайдеров. Добавьте провайдера или выберите для сессии другую "
                "модель."
            )
        if model.availability == "not_listed":
            raise ModelUnavailableError(
                f"Модель «{model.identifier}» недоступна: провайдер {model.provider} её не "
                "предоставляет. Проверьте идентификатор модели или выберите для сессии другую "
                "модель."
            )
        return model

    async def reject(self, session_id: str, text: str, error: ModelTurnError) -> None:
        """Записывает сообщение вместе с отказом хода из-за модели. Ход не запускается, и
        сессия остаётся в прежнем состоянии. Освобождает сессию, занятую методом `claim`."""
        try:
            await self._journal.append(session_id, UserMessageEvent(text=text))
            await self._journal.append(
                session_id, TurnFailedEvent(reason=error.reason, message=error.message)
            )
        finally:
            self.release(session_id)

    async def submit(self, session_id: str, text: str, model: ModelChoice) -> None:
        """Принимает сообщение и запускает ход чат-сессии. Возвращает управление сразу:
        результат приходит событиями журнала. Сессия должна быть занята методом `claim`."""
        await self._journal.append(session_id, UserMessageEvent(text=text))
        await self._run(session_id, text, model, _Discipline("chat", terminal=False))

    async def submit_task(
        self,
        session_id: str,
        task: str,
        tools: Sequence[str] | None,
        traceparent: str | None,
        result_schema: Mapping[str, Any] | None = None,
    ) -> None:
        """Принимает постановку задачи и запускает агентскую сессию.

        Постановка записывается тем же событием, что и сообщение человека: для сборки диалога
        это одно и то же. Если модели нет, сессия завершается с отказом: исполнить задание ей
        нечем, а подставлять другую модель нельзя.
        """
        try:
            model = await self.prepare(session_id)
        except ModelTurnError as error:
            await self.reject(session_id, task, error)
            await self._journal.settle(session_id, SessionFailedEvent(message=error.message))
            return
        except Exception as error:
            self.release(session_id)
            await self._journal.settle(session_id, SessionFailedEvent(message=_describe(error)))
            return
        await self._journal.append(session_id, UserMessageEvent(text=task))
        await self._run(
            session_id,
            task,
            model,
            _Discipline(
                "task",
                terminal=True,
                tools=tools,
                traceparent=traceparent,
                result_schema=result_schema,
            ),
        )

    async def wait(self, session_id: str) -> None:
        """Дожидается записи исхода хода, если ход идёт в этом процессе.

        Ход, завершившийся до вызова, уже записал исход, и ждать нечего.
        """
        turn = self._turns.get(session_id)
        if turn is not None:
            await turn.finished.wait()

    async def interrupt(self, session_id: str) -> bool:
        """Прерывает ход и дожидается записи его исхода. Отмена доходит до запроса к модели,
        поэтому генерация не продолжается."""
        turn = self._turns.get(session_id)
        if turn is None:
            return False
        turn.task.cancel()
        await turn.finished.wait()
        return True

    async def shutdown(self) -> None:
        """Остановка сервиса: идущие ходы прерываются, и их исход записывается сразу."""
        self._stopping = True
        turns = list(self._turns.values())
        for turn in turns:
            turn.task.cancel()
        if turns:
            with contextlib.suppress(TimeoutError):
                async with asyncio.timeout(_SHUTDOWN_WAIT_S):
                    await asyncio.gather(*(turn.finished.wait() for turn in turns))

    async def measure_context(self, session_id: str) -> SessionContextResponse:
        """Состав контекста сессии, оценённый токенизатором текущей модели сессии.

        Постоянная часть берётся из снимка последнего запроса: по нему видно, что модель
        получала на самом деле. Пока обращений к модели не было, постоянная часть собирается
        из нынешнего набора — того, что получит первый запрос.
        """
        async with self._db.read() as session:
            row = await session.get(SessionRow, session_id)
        if row is None:
            raise NotFoundError(f"Сессия {session_id} не найдена")
        model = await self._models.for_turn(row.model_id, row.model_provider, row.model_identifier)
        events = await self._journal.read(session_id)
        snapshot = await self._last_snapshot(events) or await self._current_snapshot(
            session_id, "task" if row.kind == "agent" else "chat", row.system_prompt
        )
        return measure_context(
            ContextInput(model=model.identifier, snapshot=snapshot, events=events)
        )

    async def _last_snapshot(self, events: Sequence[SessionEvent]) -> RequestSnapshotContent | None:
        for event in reversed(events):
            if isinstance(event, StepStartedEvent):
                if event.snapshot_id is None:
                    return None
                return await self._snapshots.find(event.snapshot_id)
        return None

    async def _current_snapshot(
        self, session_id: str, completion: CompletionMode, custom_prompt: str | None
    ) -> RequestSnapshotContent:
        """Постоянная часть, которую получил бы запрос, начнись ход сейчас. Соединения с
        серверами MCP не открываются: набор строится из сохранённых составов."""
        registry = await self._tools.for_session(session_id)
        try:
            return request_snapshot(
                completion=completion,
                prompt=_prompt(completion, custom_prompt),
                sections=registry.instructions,
                tools=sourced_tools(registry),
            )
        finally:
            await registry.close()

    async def _run(
        self, session_id: str, text: str, model: ModelChoice, discipline: _Discipline
    ) -> None:
        # Сообщение, на которое отвечает ход, к этому моменту уже записано в журнал. Отказ до
        # запуска задачи оставил бы ход незакрытым, а сессию — в состоянии `running`. Поэтому
        # такой отказ записывается как отказ хода, а созданное до него освобождается сразу.
        span: Span | None = None
        registry: SessionToolRegistry | None = None
        try:
            async with self._db.read() as session:
                row = await session.get(SessionRow, session_id)
            if row is None:
                raise NotFoundError(f"Сессия {session_id} не найдена")
            await self._set_status(session_id, "running")
            history = await self._journal.read(session_id)

            # Родитель спана берётся по порождающей сессии: дерево спанов строится из дерева
            # сессий, которое сервису и так известно.
            parent = self._traces.parent_for(row.parent_id, discipline.traceparent)
            span = tracer().start_span(
                f"agent {text[:60]}" if row.kind == "agent" else "invoke_agent", context=parent
            )
            if span.is_recording():
                span.set_attributes(
                    {
                        **session_attributes(session_id),
                        "gen_ai.operation.name": "invoke_agent",
                        "openinference.span.kind": "AGENT",
                        "hackneft.session.kind": row.kind,
                        "gen_ai.request.model": model.identifier,
                        **turn_input_attributes(text, self._capture),
                    }
                )
            context = self._traces.open(session_id, span, parent)

            # Набор инструментов собирается до обращения к модели и живёт ровно ход: вместе с
            # ним живут и соединения с серверами MCP, которые он открывает по первому вызову.
            registry = await self._tools.for_session(session_id, discipline.tools)
            provider = self._providers.require(model.provider)

            deps = TurnDeps(
                model=TracedModelClient(
                    provider=provider,
                    identifier=model.identifier,
                    settings=ChatSettings(self._agent.temperature, self._agent.max_tokens),
                    session_id=session_id,
                    parent=context,
                    capture=self._capture,
                ),
                tools=registry,
                journal=self._journal.for_session(session_id),
                snapshots=self._snapshots,
                observer=TracingToolObserver(session_id, context, self._capture),
            )
            options = TurnOptions(
                provider=model.provider,
                model=model.identifier,
                max_steps=self._agent.max_steps,
                tool_result_max_chars=self._agent.tool_result_max_chars,
                # Инструкции подключённых серверов идут секциями после указаний сервиса:
                # обещания автора внешнего сервера не должны их вытеснять.
                sections=registry.instructions,
                completion=discipline.completion,
                system_prompt=_prompt(discipline.completion, row.system_prompt),
                result_schema=discipline.result_schema,
            )

            turn = _Turn(
                task=asyncio.create_task(
                    self._execute(session_id, history, options, deps, registry),
                    name=f"turn-{session_id}",
                ),
                span=span,
                terminal=discipline.terminal,
                started_at=time.monotonic(),
            )
            self._turns[session_id] = turn
            turn.task.add_done_callback(lambda _task: self._schedule_finish(session_id, turn))
        except Exception as error:
            await self._fail_to_start(session_id, error, span, registry, discipline.terminal)

    async def _execute(
        self,
        session_id: str,
        history: Sequence[SessionEvent],
        options: TurnOptions,
        deps: TurnDeps,
        registry: SessionToolRegistry,
    ) -> TurnResult:
        """Тело задачи хода.

        Соединения хода закрываются здесь, а не в цикле: цикл о происхождении инструментов не
        осведомлён. И закрываются они в той же задаче, где открыты, как требует SDK MCP.
        """
        try:
            return await run_turn(history, options, deps)
        finally:
            # Длительность фиксируется до закрытия соединений: оно к ходу уже не относится.
            turn = self._turns.get(session_id)
            if turn is not None:
                turn.ended_at = time.monotonic()
            try:
                await registry.close()
            except Exception as error:
                logger.warning("закрытие соединений сессии %s: %s", session_id, error)

    def _schedule_finish(self, session_id: str, turn: _Turn) -> None:
        finisher = asyncio.create_task(self._finish(session_id, turn))
        self._finishers.add(finisher)
        finisher.add_done_callback(self._finishers.discard)

    async def _finish(self, session_id: str, turn: _Turn) -> None:
        try:
            await self._record_outcome(session_id, turn)
        except Exception:
            # Отказ записи исхода не должен завершать процесс вместе с идущими ходами.
            logger.exception("исход хода сессии %s не записан", session_id)
        finally:
            if self._turns.get(session_id) is turn:
                del self._turns[session_id]
            self.release(session_id)
            turn.finished.set()

    async def _record_outcome(self, session_id: str, turn: _Turn) -> None:
        ended_at = turn.ended_at if turn.ended_at is not None else time.monotonic()
        duration_ms = int((ended_at - turn.started_at) * 1000)
        self._traces.close(session_id)
        span = turn.span
        try:
            failure = _classify(turn.task, self._stopping)
            if failure is not None:
                reason, message = failure
                await self._journal.append(
                    session_id,
                    TurnFailedEvent(reason=reason, message=message, duration_ms=duration_ms),
                )
                if span.is_recording():
                    span.set_attributes(turn_output_attributes(False, message, self._capture))
                span.set_status(Status(StatusCode.ERROR, message))
                error = None if turn.task.cancelled() else turn.task.exception()
                if reason == "internal" and error is not None:
                    logger.error(
                        "сессия %s: %s",
                        session_id,
                        "".join(traceback.format_exception(error)),
                    )
                if turn.terminal:
                    await self._journal.settle(session_id, SessionFailedEvent(message=message))
                return

            result = turn.task.result()
            span.set_status(Status(StatusCode.OK))
            if span.is_recording():
                span.set_attributes(turn_output_attributes(True, result.text, self._capture))
            if not turn.terminal:
                return
            # Вопрос в агентской сессии задать некому. Модель может задать его вопреки промпту,
            # и тогда исход считается отказом: вернуть вызывающей стороне вопрос вместо
            # результата хуже, чем сообщить, что результата нет.
            if result.finish == "question":
                await self._journal.settle(
                    session_id,
                    SessionFailedEvent(
                        message=f"Агент задал уточняющий вопрос, а отвечать некому: {result.text}"
                    ),
                )
                return
            await self._journal.settle(
                session_id,
                SessionCompletedEvent(result=result.text if result.data is None else result.data),
            )
        finally:
            span.end()
            if not turn.terminal:
                with contextlib.suppress(Exception):
                    await self._set_status(session_id, "idle")

    async def _fail_to_start(
        self,
        session_id: str,
        error: Exception,
        span: Span | None,
        registry: SessionToolRegistry | None,
        terminal: bool,
    ) -> None:
        """Отказ до запуска задачи. Записывается так же, как отказ исполнения: без
        закрывающего события ход в журнале показывался бы идущим."""
        message = _describe(error)
        logger.error(
            "сессия %s: ход не запущен: %s", session_id, "".join(traceback.format_exception(error))
        )
        self._traces.close(session_id)
        if span is not None:
            span.set_status(Status(StatusCode.ERROR, message))
            span.end()
        if registry is not None:
            with contextlib.suppress(Exception):
                await registry.close()
        self.release(session_id)
        await self._journal.append(session_id, TurnFailedEvent(reason="internal", message=message))
        if terminal:
            await self._journal.settle(session_id, SessionFailedEvent(message=message))
            return
        await self._set_status(session_id, "idle")

    async def _set_status(self, session_id: str, status: str) -> None:
        async with self._db.write() as tx:
            await tx.execute(
                update(SessionRow).where(SessionRow.id == session_id).values(status=status)
            )


def _classify(
    task: "asyncio.Task[TurnResult]", stopping: bool
) -> tuple[TurnFailureReason, str] | None:
    """Различает исходы неудачного хода.

    Сведённые в одно «ошибка», они требуют разной реакции: предел шагов означает слишком
    крупную задачу, отказ модели — проблему у провайдера, прерывание — намеренное действие.
    """
    if task.cancelled():
        return ("internal", STOPPED) if stopping else ("aborted", _ABORTED)
    error = task.exception()
    if error is None:
        return None
    if isinstance(error, StepLimitReached):
        return "step_limit", describe_turn_error(error)
    if isinstance(error, OutputBudgetExhausted):
        return "output_limit", describe_turn_error(error)
    if isinstance(error, TurnError):
        return "model_error", describe_turn_error(error)
    return "internal", _describe(error)


def _prompt(completion: CompletionMode, custom: str | None) -> str | None:
    """Промпт сессии, созданной по карточке агента. Пусто — промпт сервиса по умолчанию."""
    return None if custom is None else system_prompt(completion, custom)


def _describe(error: BaseException) -> str:
    message = getattr(error, "message", None)
    if isinstance(message, str):
        return message
    text = str(error)
    return f"{type(error).__name__}: {text}" if text else type(error).__name__
