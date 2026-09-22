"""Журнал и единый допуск: все причины объединяются, интервал не обходится."""

from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from hackneft_platform.catalog import SULFUR_LIMS_CODE
from hackneft_platform.events import SensorDataCreated

from .rules import TRACKED, Monitor, Policy, Reading, Reason, evaluate
from .schema import AdvisorEvent, AdvisorInput, AdvisorRun, AdvisorState


def local_time(value: datetime) -> datetime:
    """Совместимо с существующими местными метками sensor_data."""
    return value.astimezone().replace(tzinfo=None) if value.tzinfo else value


def record_input(
    db: Session,
    events: Iterable[SensorDataCreated],
    *,
    observed_at: datetime | None = None,
    complete: bool = True,
) -> None:
    """Вызывается ДО commit измерений: потеря BackgroundTasks не теряет обработку."""
    readings = [
        Reading(id=e.data_id, code=e.sensor_code, at=local_time(e.timestamp), value=e.value)
        for e in events
        if e.sensor_code in TRACKED
    ]
    if not readings and observed_at is None:
        return
    # Старый клиент не присылает время окна. ЛИМС уже задержан агрегатором,
    # поэтому здесь восстанавливаем время доступности, а не ждём ещё четыре часа.
    available = [
        r.at + (timedelta(hours=4) if r.code == SULFUR_LIMS_CODE else timedelta()) for r in readings
    ]
    at = local_time(observed_at) if observed_at is not None else max(available)
    if any(r.at > at for r in readings):
        raise ValueError("Время измерения не может быть позже времени окна")
    db.add(
        AdvisorInput(
            observed_at=at,
            received_at=datetime.now(),
            readings=[r.model_dump(mode="json") for r in readings],
            complete=complete,
        )
    )


class AdvisorService:
    def __init__(self, sessions: sessionmaker[Session], policy: Policy) -> None:
        self.sessions = sessions
        self.policy = policy

    @staticmethod
    def _state(db: Session) -> AdvisorState:
        row = db.get(AdvisorState, 1)
        if row is None:
            row = AdvisorState(id=1, payload=Monitor().model_dump(mode="json"))
            db.add(row)
            db.flush()
        return row

    def process(self, wall_now: datetime | None = None) -> None:
        """Последовательно обрабатывает сохранённые пакеты; ИИ здесь не вызывается."""
        with self.sessions() as db:
            # SQLite: одна транзакция защищает состояние и потребление входов
            # также при двух работниках или одновременном вызове API.
            db.execute(text("BEGIN IMMEDIATE"))
            row = self._state(db)
            state = Monitor.model_validate(row.payload)
            inputs = db.scalars(
                select(AdvisorInput)
                .where(
                    AdvisorInput.processed.is_(False),
                )
                .order_by(AdvisorInput.id)
                .limit(500)
            ).all()
            for incoming in inputs:
                new_lab = False
                for raw in incoming.readings:
                    reading = Reading.model_validate(raw)
                    previous = state.latest.get(reading.code)
                    if previous is not None and reading.at <= previous.at:
                        continue  # Поздняя история не подменяет текущий режим.
                    state.latest[reading.code] = reading
                    history = state.history.setdefault(reading.code, [])
                    history.append(reading)
                    cutoff = reading.at - timedelta(hours=2)
                    state.history[reading.code] = [r for r in history if r.at >= cutoff]
                    new_lab |= reading.code == SULFUR_LIMS_CODE
                state.at = max(state.at or incoming.observed_at, incoming.observed_at)
                state.window_open = not incoming.complete
                # Лабораторный факт сохраняется до финального маркера окна.
                if new_lab:
                    state.new_lab_pending = True
                if incoming.complete:
                    self._evaluate(db, state)
                incoming.processed = True
            if self.policy.clock == "live" and state.at is not None and not state.window_open:
                now = local_time(wall_now or datetime.now())
                if now > state.at:
                    state.at = now
                    self._evaluate(db, state)
            row.payload = state.model_dump(mode="json")
            db.commit()

    def _evaluate(self, db: Session, state: Monitor) -> None:
        new_lab = state.new_lab_pending
        state.new_lab_pending = False
        for reason in evaluate(state, self.policy, new_lab=new_lab):
            db.add(
                AdvisorEvent(
                    kind=reason.kind,
                    occurred_at=state.at,
                    severity=reason.severity,
                    reason=reason.reason,
                    consequence=reason.consequence,
                    evidence=reason.evidence,
                )
            )
        state.revision += 1

    def reserve(self) -> str | None:
        """Создаёт не больше одного запуска за интервал, независимо от числа событий."""
        with self.sessions() as db:
            db.execute(text("BEGIN IMMEDIATE"))
            row = self._state(db)
            state = Monitor.model_validate(row.payload)
            remaining = db.scalar(
                select(AdvisorInput.id)
                .where(
                    AdvisorInput.processed.is_(False),
                )
                .limit(1)
            )
            if (
                state.at is None
                or state.window_open
                or state.bad_codes
                or not state.pending
                or row.active_run_id is not None
                or remaining is not None
            ):
                db.commit()
                return None
            if row.last_started_at is not None and state.at < (
                row.last_started_at + timedelta(minutes=self.policy.interval_minutes)
            ):
                db.commit()
                return None
            run_id = str(uuid4())
            # Активный риск всегда включается в контекст, даже если новый повод — ЛИМС.
            reasons = state.active | state.pending
            db.add(
                AdvisorRun(
                    id=run_id,
                    requested_at=state.at,
                    status="queued",
                    reasons=[r.model_dump(mode="json") for r in reasons.values()],
                    snapshot=self._snapshot(state),
                )
            )
            row.payload = state.model_dump(mode="json")
            row.active_run_id = run_id
            # Считаем с допуска: даже ошибка транспорта не создаёт шторм повторов.
            row.last_started_at = state.at
            db.commit()
            return run_id

    def _snapshot(self, state: Monitor) -> dict[str, Any]:
        return {
            "at": state.at.isoformat() if state.at else None,
            "unit": "24-2000",
            "readings": {k: r.model_dump(mode="json") for k, r in state.latest.items()},
            "recent_history": {
                k: [r.model_dump(mode="json") for r in history]
                for k, history in state.history.items()
            },
            "sulfur_level": state.sulfur_level,
            "policy": self.policy.model_dump(mode="json"),
        }

    def claim(self, run_id: str) -> dict[str, Any] | None:
        with self.sessions() as db:
            db.execute(text("BEGIN IMMEDIATE"))
            run = db.get(AdvisorRun, run_id)
            if run is None or run.status != "queued":
                return None
            state_row = self._state(db)
            state = Monitor.model_validate(state_row.payload)
            remaining = db.scalar(
                select(AdvisorInput.id)
                .where(
                    AdvisorInput.processed.is_(False),
                )
                .limit(1)
            )
            # Между допуском и отправкой мог прийти новый неполный пакет.
            if state.bad_codes or state.window_open or remaining is not None:
                return None
            if not state.pending:
                run.status = "cancelled"
                run.error = "Причина исчезла до отправки"
                run.finished_at = datetime.now()
                state_row.active_run_id = None
                db.commit()
                return None
            run.snapshot = self._snapshot(state)
            run.reasons = [
                r.model_dump(mode="json") for r in (state.active | state.pending).values()
            ]
            state.pending.clear()
            state.reviewed_sulfur_level = state.sulfur_level
            state_row.payload = state.model_dump(mode="json")
            state_row.last_started_at = state.at
            run.status = "sending"
            run.started_at = datetime.now()
            data = self._run_dict(run)
            db.commit()
            return data

    def update_run(
        self,
        run_id: str,
        status: str,
        *,
        session_id: str | None = None,
        result: Any = None,
        error: str | None = None,
    ) -> None:
        with self.sessions() as db:
            db.execute(text("BEGIN IMMEDIATE"))
            run = db.get(AdvisorRun, run_id)
            if run is None:
                return
            run.status = status
            if session_id is not None:
                run.session_id = session_id
            run.error = error
            run.result = result
            if status in ("completed", "failed", "cancelled"):
                run.finished_at = datetime.now()
                row = self._state(db)
                if row.active_run_id == run_id:
                    row.active_run_id = None
                if status == "failed":
                    # Известный отказ можно повторить в следующем общем интервале.
                    # Более новые причины, пришедшие за время выполнения, сохраняются.
                    state = Monitor.model_validate(row.payload)
                    for raw in run.reasons:
                        reason = Reason.model_validate(raw)
                        if reason.kind == "sulfur_risk" and state.sulfur_level == "normal":
                            continue
                        state.pending.setdefault(reason.kind, reason)
                    row.payload = state.model_dump(mode="json")
            db.commit()

    def active_run(self) -> dict[str, Any] | None:
        with self.sessions() as db:
            row = db.get(AdvisorState, 1)
            run = db.get(AdvisorRun, row.active_run_id) if row and row.active_run_id else None
            return self._run_dict(run) if run else None

    @staticmethod
    def _run_dict(run: AdvisorRun) -> dict[str, Any]:
        return {
            "id": run.id,
            "status": run.status,
            "requested_at": run.requested_at,
            "started_at": run.started_at,
            "finished_at": run.finished_at,
            "reasons": run.reasons,
            "snapshot": run.snapshot,
            "session_id": run.session_id,
            "result": run.result,
            "error": run.error,
        }

    def status(self) -> dict[str, Any]:
        with self.sessions() as db:
            row = db.get(AdvisorState, 1)
            state = Monitor.model_validate(row.payload) if row else Monitor()
            last_run = db.scalar(
                select(AdvisorRun).order_by(AdvisorRun.requested_at.desc()).limit(1)
            )
            return {
                "unit": "24-2000",
                "clock": self.policy.clock,
                "observed_at": state.at,
                "interval_minutes": self.policy.interval_minutes,
                "next_allowed_at": (
                    row.last_started_at + timedelta(minutes=self.policy.interval_minutes)
                    if row and row.last_started_at
                    else None
                ),
                "blocked_by_data": state.bad_codes,
                "window_open": state.window_open,
                "active_reasons": [r.model_dump() for r in state.active.values()],
                "pending_reasons": [r.model_dump() for r in state.pending.values()],
                "active_run_id": row.active_run_id if row else None,
                "last_run": self._run_dict(last_run) if last_run else None,
                "result_needs_review": bool(state.bad_codes or state.pending),
            }

    def events(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.sessions() as db:
            rows = db.scalars(select(AdvisorEvent).order_by(AdvisorEvent.id.desc()).limit(limit))
            return [
                {
                    "id": r.id,
                    "kind": r.kind,
                    "occurred_at": r.occurred_at,
                    "severity": r.severity,
                    "reason": r.reason,
                    "consequence": r.consequence,
                    "evidence": r.evidence,
                }
                for r in rows
            ]
