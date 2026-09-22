"""Постоянный журнал входов, событий и запусков. Измерения остаются в sensor_data."""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from hackneft_platform.db import Base


class AdvisorInput(Base):
    __tablename__ = "advisor_inputs"

    id: Mapped[int] = mapped_column(primary_key=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime)
    received_at: Mapped[datetime] = mapped_column(DateTime)
    readings: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    complete: Mapped[bool]
    processed: Mapped[bool] = mapped_column(default=False, index=True)


class AdvisorState(Base):
    __tablename__ = "advisor_state"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    last_started_at: Mapped[datetime | None] = mapped_column(DateTime)
    active_run_id: Mapped[str | None] = mapped_column(String)


class AdvisorEvent(Base):
    __tablename__ = "advisor_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String, index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime)
    severity: Mapped[str] = mapped_column(String)
    reason: Mapped[str]
    consequence: Mapped[str]
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON)


class AdvisorRun(Base):
    __tablename__ = "advisor_runs"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String, index=True)
    reasons: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON)
    session_id: Mapped[str | None] = mapped_column(String)
    result: Mapped[Any | None] = mapped_column(JSON)
    error: Mapped[str | None]
