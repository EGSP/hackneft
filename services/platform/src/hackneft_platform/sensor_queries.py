"""Выборки показаний датчиков для агентов.

Общее правило всех выборок: окно отсчитывается назад от курсора, `(cursor − window, cursor]`.
Показания позже курсора не выдаются никогда — агент, анализирующий момент истории, не должен
видеть будущее. Курсор по умолчанию — время последнего сохранённого показания, то есть текущий
момент симуляции.

Если в окне нет ни одного показания датчика, к ответу добавляется последнее показание до окна
с его собственной отметкой времени. Редкие ряды (лабораторные анализы — раз в сутки) иначе
выглядели бы для агента отсутствующими, хотя последнее известное значение у них есть.

Показания отдаются как есть, без усреднения и прореживания.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from hackneft_platform.catalog import SENSOR_SYNONYMS, SOURCE_NAMES
from hackneft_platform.models import SensorData, SensorName

MAX_SENSORS = 12
"""Предел числа датчиков в одном запросе. Ограничивает объём ответа, а значит и расход токенов."""
MAX_WINDOW = timedelta(days=31)


class QueryError(ValueError):
    """Неверный запрос агента. Текст объясняет, как его исправить."""


@dataclass(frozen=True, slots=True)
class Point:
    timestamp: datetime
    value: float


@dataclass(frozen=True, slots=True)
class SensorWindow:
    """Показания одного датчика в окне."""

    requested: str
    """Имя, по которому датчик запрошен."""
    code: str
    title: str | None
    points: list[Point]
    """Показания в окне по возрастанию времени."""
    last_before: Point | None
    """Последнее показание до окна. Заполняется, только если в окне показаний нет."""


@dataclass(frozen=True, slots=True)
class WindowQuery:
    cursor: datetime
    start: datetime
    sensors: list[SensorWindow]
    unknown: list[str]
    """Имена, не найденные в справочнике."""


def fmt_time(moment: datetime) -> str:
    return moment.isoformat(sep=" ", timespec="minutes")


def title_of(code: str) -> str | None:
    """Описательное имя кода из каталога — первое из синонимов."""
    names = SENSOR_SYNONYMS.get(code)
    return names[0] if names else None


def latest_timestamp(db: Session) -> datetime | None:
    return db.scalar(select(func.max(SensorData.timestamp)))


def parse_cursor(db: Session, cursor: str | None) -> datetime:
    if cursor is None or cursor.strip() == "":
        latest = latest_timestamp(db)
        if latest is None:
            raise QueryError("В базе платформы ещё нет ни одного показания.")
        return latest
    try:
        moment = datetime.fromisoformat(cursor.strip().replace(" ", "T", 1))
    except ValueError as error:
        raise QueryError(
            f"Курсор {cursor!r} не разобран. Ожидается дата вида 2023-01-02T10:00."
        ) from error
    return moment.replace(tzinfo=None)


def _all_names(db: Session) -> list[tuple[str, str]]:
    """Все пары «код — имя» справочника. Справочник мал (сотни строк), поэтому сопоставление
    идёт в Python: функция lower() в SQLite не переводит в нижний регистр кириллицу."""
    rows = db.execute(select(SensorName.sensor_code, SensorName.name)).all()
    return [(row.sensor_code, row.name) for row in rows]


def resolve_names(db: Session, names: Sequence[str]) -> tuple[dict[str, str], list[str]]:
    """Сопоставляет имена кодам датчиков. Имя — код или любой синоним из справочника.

    Сравнение без учёта регистра: агент пишет коды по-разному (`HT_Q21`, `ht_q21`).
    """
    requested = list(dict.fromkeys(name.strip() for name in names if name.strip()))
    if not requested:
        raise QueryError("Не указано ни одного датчика.")
    if len(requested) > MAX_SENSORS:
        raise QueryError(f"Не более {MAX_SENSORS} датчиков за один вызов.")
    code_by_name: dict[str, str] = {}
    for code, name in _all_names(db):
        code_by_name.setdefault(name.casefold(), code)
    codes = {name: code_by_name[name.casefold()] for name in requested if name.casefold() in code_by_name}
    unknown = [name for name in requested if name not in codes]
    return codes, unknown


def query_window(
    db: Session, names: Sequence[str], window_minutes: float, cursor: str | None
) -> WindowQuery:
    if window_minutes <= 0:
        raise QueryError("Окно должно быть положительным числом минут.")
    window = timedelta(minutes=window_minutes)
    if window > MAX_WINDOW:
        raise QueryError(f"Окно не может превышать {MAX_WINDOW.days} суток.")

    moment = parse_cursor(db, cursor)
    start = moment - window
    codes, unknown = resolve_names(db, names)

    sensors: list[SensorWindow] = []
    for requested, code in codes.items():
        rows = db.execute(
            select(SensorData.timestamp, SensorData.value)
            .where(
                SensorData.sensor_code == code,
                SensorData.timestamp > start,
                SensorData.timestamp <= moment,
            )
            .order_by(SensorData.timestamp)
        ).all()
        points = [Point(row.timestamp, row.value) for row in rows]
        last_before = None
        if not points:
            row = db.execute(
                select(SensorData.timestamp, SensorData.value)
                .where(SensorData.sensor_code == code, SensorData.timestamp <= start)
                .order_by(desc(SensorData.timestamp))
                .limit(1)
            ).first()
            if row is not None:
                last_before = Point(row.timestamp, row.value)
        sensors.append(
            SensorWindow(
                requested=requested,
                code=code,
                title=title_of(code),
                points=points,
                last_before=last_before,
            )
        )
    return WindowQuery(cursor=moment, start=start, sensors=sensors, unknown=unknown)


def search_catalog(
    db: Session, query: str, limit: int, only_with_synonyms: bool
) -> list[dict[str, object]]:
    """Поиск датчиков по словам запроса в коде и именах.

    `only_with_synonyms` оставляет датчики, у которых есть синоним сверх исходных названий
    (catalog.SOURCE_NAMES): справочник насчитывает сотни датчиков, а выделенных по назначению —
    единицы. Пустой запрос перечисляет все датчики, прошедшие отбор.

    Датчики упорядочены по числу совпавших слов, а не отбираются по совпадению всех: агент
    пишет запрос своими словами («сера дизель»), и одно слово, которого нет в именах («ДТ»
    вместо «дизель»), не должно обнулять выдачу. Слово засчитывается и по началу: «серы»
    совпадает с «сера» по общей основе из первых четырёх букв.
    """
    words = [word.casefold() for word in query.split() if word.strip()]
    stems = [word[:4] if len(word) > 4 else word for word in words]
    by_code: dict[str, list[str]] = {}
    for code, name in _all_names(db):
        names = by_code.setdefault(code, [])
        if name != code:
            names.append(name)
    scored = []
    for code, names in by_code.items():
        if only_with_synonyms:
            source = SOURCE_NAMES.get(code, frozenset({code}))
            if all(name in source for name in names):
                continue
        text = " ".join([code, *names]).casefold()
        score = sum(1 for stem in stems if stem in text)
        if stems and score == 0:
            continue
        scored.append((-score, code))
    scored.sort()
    return [
        {"code": code, **({"matched": -score} if stems else {}), "names": sorted(by_code[code])}
        for score, code in scored[:limit]
    ]
