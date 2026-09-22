"""Последовательные, объяснимые проверки. Здесь нет вызовов ИИ и управления установкой."""

import math
from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field

from hackneft_platform.catalog import SULFUR_LIMS_CODE, SULFUR_PAK_CODE

REQUIRED = (SULFUR_PAK_CODE, "ht_t6", "ht_f9", "ht_f25", "ht_f2", "ht_p13")
TRACKED = (*REQUIRED, SULFUR_LIMS_CODE, "ht_q20")

SULFUR_THRESHOLD = 10.0
"""Порог серы в продукте, мг/кг: выше него топливо не соответствует требованию."""

# Названия и единицы параметров для текстов причин. Единицы расхода газа в материалах
# проекта не подтверждены, поэтому для них единица не указывается.
NAMES = {
    SULFUR_PAK_CODE: "сера по ПАК",
    SULFUR_LIMS_CODE: "сера по ЛИМС",
    "ht_q20": "сера в сырье (Q20)",
    "ht_f9": "подача сырья (F9)",
    "ht_t6": "температура на входе в реактор (T6)",
    "ht_p13": "давление на входе в реактор (P13)",
    "ht_f25": "свежий ВСГ (F25)",
    "ht_f2": "циркуляционный газ (F2)",
}
UNITS = {
    SULFUR_PAK_CODE: "мг/кг",
    SULFUR_LIMS_CODE: "мг/кг",
    "ht_q20": "мг/кг",
    "ht_f9": "т/ч",
    "ht_t6": "°C",
    "ht_p13": "МПа",
}


def number(value: float) -> str:
    """Число для текста причины: запятая как десятичный знак, точность по величине."""
    size = abs(value)
    digits = 0 if size >= 1000 else 1 if size >= 100 else 2 if size >= 1 else 3
    return f"{value:.{digits}f}".replace(".", ",")


def quantity(code: str, value: float) -> str:
    unit = UNITS.get(code)
    return f"{number(value)} {unit}" if unit else number(value)


def signed(value: float) -> str:
    return ("+" if value > 0 else "−" if value < 0 else "±") + number(abs(value))


def title(name: str) -> str:
    """Название с прописной буквы; в отличие от str.capitalize, код в скобках не меняется."""
    return name[:1].upper() + name[1:]


def percent(before: float, after: float) -> str:
    return f" ({signed((after - before) / before * 100)} %)" if before else ""


class Policy(BaseModel):
    interval_minutes: int = Field(default=30, ge=15, le=60)
    stale_minutes: int = Field(default=30, ge=10)
    warning: float = Field(default=8.0, gt=0, lt=10)
    warning_minutes: int = Field(default=30, ge=10)
    sulfur_increase: float = Field(default=1.0, gt=0)
    change_fraction: float = Field(default=0.10, gt=0, le=1)
    temperature_change: float = Field(default=5.0, gt=0)
    pressure_change: float = Field(default=0.2, gt=0)
    clock: Literal["replay", "live"] = "replay"


class Reading(BaseModel):
    id: int
    code: str
    at: datetime
    value: float

    def valid(self) -> bool:
        return math.isfinite(self.value) and self.value >= 0 and self.value != 307


class Reason(BaseModel):
    kind: str
    severity: Literal["info", "warning", "critical"]
    reason: str
    consequence: str
    evidence: dict[str, float | str | list[str]] = Field(default_factory=dict)


class Monitor(BaseModel):
    at: datetime | None = None
    latest: dict[str, Reading] = Field(default_factory=dict)
    history: dict[str, list[Reading]] = Field(default_factory=dict)
    baselines: dict[str, float] = Field(default_factory=dict)
    # Одна ожидающая причина каждого вида; несколько видов попадут в один запуск.
    pending: dict[str, Reason] = Field(default_factory=dict)
    active: dict[str, Reason] = Field(default_factory=dict)
    sulfur_level: str = "normal"
    reviewed_sulfur_level: str = "normal"
    bad_codes: list[str] = Field(default_factory=list)
    window_open: bool = False
    new_lab_pending: bool = False
    revision: int = 0


def fresh(reading: Reading | None, at: datetime, policy: Policy) -> bool:
    return (
        reading is not None
        and reading.valid()
        and timedelta(0) <= at - reading.at <= timedelta(minutes=policy.stale_minutes)
    )


def sustained(history: list[Reading], at: datetime, minutes: int, threshold: float) -> bool:
    """Точки покрывают весь интервал; пропуск длиннее 10 минут разрывает подтверждение."""
    start = at - timedelta(minutes=minutes)
    points = [r for r in history if r.at >= start]
    if not points or points[0].at > start or points[-1].at != at:
        return False
    return all(r.valid() and r.value >= threshold for r in points) and all(
        b.at - a.at <= timedelta(minutes=10) for a, b in zip(points, points[1:], strict=False)
    )


def change_over(history: list[Reading], at: datetime, minutes: int) -> float | None:
    """Изменение значения за последние `minutes` минут; пусто, если точек меньше двух."""
    start = at - timedelta(minutes=minutes)
    points = [r for r in history if r.valid() and start <= r.at <= at]
    if len(points) < 2 or points[-1].at != at:
        return None
    return points[-1].value - points[0].value


def evaluate(state: Monitor, policy: Policy, *, new_lab: bool = False) -> list[Reason]:
    """Обновляет состояние шести правил и возвращает только произошедшие изменения."""
    if state.at is None:
        return []
    at = state.at
    events: list[Reason] = []

    def emit(reason: Reason, *, launch: bool = True, active: bool = False) -> None:
        events.append(reason)
        if launch:
            state.pending[reason.kind] = reason
        if active:
            state.active[reason.kind] = reason

    bad = sorted(code for code in REQUIRED if not fresh(state.latest.get(code), at, policy))
    if bad != state.bad_codes:
        reason = Reason(
            kind="data_quality",
            severity="warning" if bad else "info",
            reason=(
                "Нет свежих достоверных показаний: "
                + ", ".join(NAMES.get(code, code) for code in bad)
                if bad
                else "Данные восстановлены: все обязательные показания свежие"
            ),
            consequence=(
                "Автоматический совет приостановлен: по показаниям старше "
                f"{policy.stale_minutes} мин, пропускам и значению 307 режим не оценить."
                if bad
                else "Режим снова оценивается по свежему снимку."
            ),
            evidence={"codes": bad},
        )
        emit(reason, launch=False, active=bool(bad))
        if not bad:
            state.active.pop("data_quality", None)
        state.bad_codes = bad

    pak = state.latest.get(SULFUR_PAK_CODE)
    if fresh(pak, at, policy) and pak is not None:
        level = state.sulfur_level
        if pak.value > 10:
            level = "exceeded"
        elif sustained(
            state.history.get(SULFUR_PAK_CODE, []),
            pak.at,
            policy.warning_minutes,
            policy.warning,
        ):
            level = "warning"
        elif pak.value < policy.warning - 0.5:
            level = "normal"
        elif level == "exceeded" and pak.value < 9.5:
            level = "warning"
        prior_risk = state.active.get("sulfur_risk")
        prior_value = prior_risk.evidence.get("value") if prior_risk else None
        worsening = (
            level != "normal"
            and isinstance(prior_value, (int, float))
            and pak.value - prior_value >= policy.sulfur_increase
        )
        if level != state.sulfur_level or worsening:
            value = number(pak.value)
            margin = SULFUR_THRESHOLD - pak.value
            minutes = policy.warning_minutes
            trend = change_over(state.history.get(SULFUR_PAK_CODE, []), pak.at, minutes)
            trend_text = f", за {minutes} мин {signed(trend)} мг/кг" if trend is not None else ""
            evidence: dict[str, float | str | list[str]] = {
                "value": pak.value,
                "sensor_code": pak.code,
                "level": level,
                "threshold": SULFUR_THRESHOLD,
                "margin": round(margin, 3),
            }
            if trend is not None:
                evidence["change"] = round(trend, 3)
                evidence["change_minutes"] = minutes
            if level == "normal":
                text = f"Сера по ПАК снизилась до {value} мг/кг{trend_text}"
                consequence = (
                    f"Запас до порога {number(margin)} мг/кг: проверить, нужны ли прежние действия."
                )
            elif level == "warning":
                text = (
                    f"Сера по ПАК {value} мг/кг: не ниже {number(policy.warning)} мг/кг "
                    f"{minutes} мин подряд{trend_text}"
                )
                consequence = (
                    f"Запас до порога {number(SULFUR_THRESHOLD)} мг/кг — {number(margin)} мг/кг."
                )
            else:
                text = (
                    f"Сера по ПАК {value} мг/кг выше порога "
                    f"{number(SULFUR_THRESHOLD)} мг/кг{trend_text}"
                )
                consequence = f"Порог превышен на {number(-margin)} мг/кг."
            reason = Reason(
                kind="sulfur_risk",
                severity="critical"
                if level == "exceeded"
                else ("warning" if level == "warning" else "info"),
                reason=text,
                consequence=consequence,
                evidence=evidence,
            )
            if level == "normal":
                state.pending.pop("sulfur_risk", None)
            emit(
                reason,
                active=level != "normal",
                launch=level != "normal" or state.reviewed_sulfur_level != "normal",
            )
            if level == "normal":
                state.active.pop("sulfur_risk", None)
            state.sulfur_level = level

    if new_lab:
        lab = state.latest[SULFUR_LIMS_CODE]
        if lab.valid():
            emit(
                Reason(
                    kind="lab_result",
                    severity="critical" if lab.value > 10 else "info",
                    reason=(
                        f"ЛИМС: сера {number(lab.value)} мг/кг в пробе от {lab.at:%d.%m %H:%M}"
                        + (
                            f", выше порога {number(SULFUR_THRESHOLD)} мг/кг"
                            if lab.value > SULFUR_THRESHOLD
                            else ""
                        )
                    ),
                    consequence=f"Сравнить с ПАК на {lab.at:%H:%M}, а не с последней точкой ПАК.",
                    evidence={"value": lab.value, "sampled_at": lab.at.isoformat()},
                )
            )

    # Простые изменения относительно последнего принятого уровня. Два соседних
    # качественных измерения подтверждают новый уровень; начальное значение — база.
    def changed(key: str, value: float, previous: float | None, threshold: float) -> bool:
        baseline = state.baselines.get(key)
        if baseline is None:
            state.baselines[key] = value
            return False
        delta = value - baseline
        if previous is None or abs(delta) < threshold:
            return False
        if (previous - baseline) * delta <= 0 or abs(previous - baseline) < threshold:
            return False
        state.baselines[key] = value
        return True

    for code, kind, threshold, consequence in (
        ("ht_q20", "feed_changed", None, "Более сернистое сырьё может увеличить серу в продукте."),
        ("ht_f9", "feed_changed", None, "Изменились нагрузка и время пребывания сырья в реакторе."),
        (
            "ht_t6",
            "regime_changed",
            policy.temperature_change,
            "Изменились условия очистки; учесть уже произошедшее воздействие.",
        ),
        (
            "ht_p13",
            "regime_changed",
            policy.pressure_change,
            "Изменились условия реакции; проверить актуальность прежнего совета.",
        ),
    ):
        reading = state.latest.get(code)
        if not fresh(reading, at, policy) or reading is None:
            continue
        history = state.history.get(code, [])
        previous = history[-2] if len(history) >= 2 else None
        prev_value = (
            previous.value if previous is not None and fresh(previous, at, policy) else None
        )
        baseline = state.baselines.get(code, reading.value)
        boundary = threshold if threshold is not None else abs(baseline) * policy.change_fraction
        if boundary <= 0:
            state.baselines[code] = reading.value
            continue
        if changed(code, reading.value, prev_value, boundary):
            emit(
                Reason(
                    kind=kind,
                    severity="warning",
                    reason=(
                        f"{title(NAMES[code])}: {quantity(code, baseline)} → "
                        f"{quantity(code, reading.value)}{percent(baseline, reading.value)}"
                    ),
                    consequence=consequence
                    if code != "ht_q20" or reading.value > baseline
                    else "Сера в сырье снизилась; уточнить необходимую интенсивность очистки.",
                    evidence={"sensor_code": code, "before": baseline, "after": reading.value},
                )
            )

    feed = state.latest.get("ht_f9")
    if fresh(feed, at, policy) and feed is not None and feed.value > 1:
        for code in ("ht_f25", "ht_f2"):
            gas = state.latest.get(code)
            if not fresh(gas, at, policy) or gas is None or gas.at != feed.at:
                continue
            key = f"{code}/ht_f9"
            ratio = gas.value / feed.value
            baseline = state.baselines.get(key, ratio)
            gh, fh = state.history.get(code, []), state.history.get("ht_f9", [])
            previous_ratio = None
            if len(gh) >= 2 and len(fh) >= 2:
                g, f = gh[-2], fh[-2]
                if g.at == f.at and fresh(g, at, policy) and fresh(f, at, policy) and f.value > 1:
                    previous_ratio = g.value / f.value
            if changed(key, ratio, previous_ratio, max(baseline * policy.change_fraction, 0.001)):
                if ratio < baseline:
                    emit(
                        Reason(
                            kind="gas_supply",
                            severity="warning",
                            reason=(
                                f"{title(NAMES[code])} на тонну сырья: "
                                f"{number(baseline)} → {number(ratio)}{percent(baseline, ratio)}"
                            ),
                            consequence=(
                                f"Очистка может ухудшиться: {NAMES[code]} {number(gas.value)} "
                                f"при подаче сырья {quantity('ht_f9', feed.value)}."
                            ),
                            evidence={"ratio": key, "before": baseline, "after": ratio},
                        )
                    )
    return events
