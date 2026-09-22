"""Исполнитель симуляции потока данных.

Курсор движется по источнику от заданной даты. За один такт читается окно
[курсор, курсор + шаг) и отправляется на платформу; после подтверждения отправки курсор
переставляется на конец окна и сохраняется. Такты разделены интервалом опроса — реальным
временем между отправками.

Интервал и шаг независимы: их отношение и есть коэффициент ускорения симуляции. При равенстве
поток идёт в реальном темпе установки (шаг выгрузки — 10 минут), при шаге больше интервала
база наполняется быстрее реального времени.

Признак работы не восстанавливается при запуске: агрегатор всегда начинает с паузы, поток
данных начинается только по команде из веб-интерфейса.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from .config import AggregatorConfig
from .platform_client import PlatformClient, PlatformUnavailable
from .source import SourceSet
from .state import SavedState, StateStore

logger = logging.getLogger(__name__)

_LOOP_STEP_S = 0.5
"""Шаг ожидания в цикле. Цикл просыпается чаще, чем наступает такт, чтобы пауза и изменение
интервала вступали в силу сразу, а не по завершении текущего ожидания."""

_PRELOAD_CHUNK = timedelta(hours=6)
"""Часть окна первичной закачки. Ограничивает объём одного чтения источников."""

_HEALTH_PERIOD_S = 15.0
"""Период проверки доступности платформы. Не зависит от интервала опроса: состояние
подключения нужно видеть и на паузе."""


@dataclass(slots=True)
class TickReport:
    """Итог последнего такта. Показывается в веб-интерфейсе."""

    at: datetime
    window_start: datetime
    window_end: datetime
    readings: int
    accepted: int
    duplicates: int
    error: str | None


class SimulationRunner:
    def __init__(self, config: AggregatorConfig) -> None:
        self._config = config
        self._sources = SourceSet.from_directory(
            config.source_dir,
            {"lims": timedelta(minutes=config.lims_delay_minutes)},
        )
        self._store = StateStore(config.state_path)
        self._platform = PlatformClient(config.platform_url, config.request_timeout_s)

        saved = self._store.load()
        self._cursor = saved.cursor if saved is not None else config.start_date
        self._interval_minutes = config.interval_minutes
        self._step_minutes = config.step_minutes

        self._running = False
        self._exhausted = False
        self._indexing = "pending"
        self._next_tick_at: datetime | None = None
        self._last_tick: TickReport | None = None
        self._ticks = 0
        self._accepted_total = 0
        self._duplicates_total = 0
        self._platform_online: bool | None = None
        self._platform_checked_at: datetime | None = None

        self._lock = asyncio.Lock()
        self._tasks: list[asyncio.Task[None]] = []

    # ─── Жизненный цикл ───────────────────────────────────────────────────────

    async def start(self) -> None:
        self._tasks = [
            asyncio.create_task(self._index_sources(), name="aggregator-index"),
            asyncio.create_task(self._simulate(), name="aggregator-simulate"),
            asyncio.create_task(self._watch_platform(), name="aggregator-health"),
        ]

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._tasks = []
        await self._platform.close()

    async def _index_sources(self) -> None:
        """Строит указатели по файлам телеметрии.

        Чтение сотен мегабайт блокирует поток, поэтому выполняется в отдельном потоке: иначе
        веб-интерфейс не отвечал бы, пока указатели не построены.
        """
        self._indexing = "running"
        await asyncio.to_thread(self._sources.build_index)
        self._indexing = "ready" if self._sources.ready else "failed"
        logger.info(
            "Указатели источников построены: %s",
            ", ".join(
                f"{status.title} — {status.row_count} строк, {status.sensor_count} тегов"
                if status.ready
                else f"{status.title} — отказ: {status.error}"
                for status in self._sources.statuses()
            ),
        )
        if self._sources.ready:
            await self._preload()

    async def _preload(self) -> None:
        """Первичная закачка истории (`AGGREGATOR_PRELOAD_HOURS`).

        Выполняется только из начального положения курсора: после перезапуска с сохранённым
        курсором история уже в платформе. Окно отправляется частями по шагу закачки теми же
        средствами, что и такт, поэтому курсор продвигается вместе с отправкой, а отказ
        платформы оставляет его на последней успешно отправленной части.
        """
        hours = self._config.preload_hours
        if hours <= 0 or self._cursor != self._config.start_date:
            return
        until = self._config.start_date + timedelta(hours=hours)
        accepted = 0
        async with self._lock:
            while self._cursor < until:
                end = min(self._cursor + _PRELOAD_CHUNK, until)
                report = await self._run_window(self._cursor, end)
                if report.error is not None:
                    logger.warning(
                        "Первичная закачка прервана на %s: %s", report.window_start, report.error
                    )
                    return
                accepted += report.accepted
                self._accepted_total += report.accepted
                self._duplicates_total += report.duplicates
        logger.info("Первичная закачка: отправлено %s показаний до %s", accepted, until)

    # ─── Управление ───────────────────────────────────────────────────────────

    async def resume(self) -> None:
        async with self._lock:
            if self._exhausted:
                return
            self._running = True
            # Первый такт выполняется сразу после снятия паузы, а не через интервал:
            # иначе нажатие кнопки не давало бы видимого отклика до нескольких минут.
            self._next_tick_at = _now()

    async def pause(self) -> None:
        async with self._lock:
            self._running = False
            self._next_tick_at = None

    async def update_settings(
        self, interval_minutes: float | None, step_minutes: float | None
    ) -> None:
        async with self._lock:
            if interval_minutes is not None:
                self._interval_minutes = interval_minutes
                if self._running:
                    # Новый интервал отсчитывается от предыдущего такта, а не от момента
                    # изменения: иначе правка настройки откладывала бы ближайший такт.
                    previous = self._last_tick.at if self._last_tick else _now()
                    self._next_tick_at = previous + self._interval
            if step_minutes is not None:
                self._step_minutes = step_minutes

    async def set_cursor(self, cursor: datetime) -> None:
        async with self._lock:
            self._cursor = cursor
            self._exhausted = False
            self._store.save(SavedState(cursor=cursor))

    async def reset_cursor(self) -> None:
        await self.set_cursor(self._config.start_date)

    # ─── Такты ────────────────────────────────────────────────────────────────

    @property
    def _interval(self) -> timedelta:
        return timedelta(minutes=self._interval_minutes)

    @property
    def _step(self) -> timedelta:
        return timedelta(minutes=self._step_minutes)

    async def _simulate(self) -> None:
        while True:
            await asyncio.sleep(_LOOP_STEP_S)
            if not self._running or self._next_tick_at is None:
                continue
            if _now() < self._next_tick_at:
                continue
            await self._tick()

    async def _tick(self) -> None:
        async with self._lock:
            start = self._cursor
            end = start + self._step
            report = await self._run_window(start, end)
            self._last_tick = report
            self._ticks += 1
            self._accepted_total += report.accepted
            self._duplicates_total += report.duplicates
            if self._running:
                self._next_tick_at = report.at + self._interval

    async def _run_window(self, start: datetime, end: datetime) -> TickReport:
        """Читает окно и отправляет его. Курсор продвигается только при успешной отправке."""
        moment = _now()
        if self._indexing != "ready":
            return TickReport(moment, start, end, 0, 0, 0, "источники не готовы")

        last = self._sources.last_timestamp
        if last is not None and start > last:
            self._exhausted = True
            self._running = False
            self._next_tick_at = None
            return TickReport(moment, start, end, 0, 0, 0, "источник исчерпан")

        readings = await asyncio.to_thread(self._sources.read, start, end)

        accepted = 0
        duplicates = 0
        limit = self._config.batch_limit
        # Пустой финальный пакет закрывает всё окно, в том числе окно без данных.
        # До него платформа не принимает частичный HTTP-пакет за полный снимок.
        chunks = [readings[p : p + limit] for p in range(0, len(readings), limit)]
        chunks.append([])
        for chunk in chunks:
            try:
                result = await self._platform.push(
                    chunk,
                    observed_at=end,
                    complete=not chunk,
                )
            except PlatformUnavailable as failure:
                self._platform_online = False
                self._platform_checked_at = _now()
                logger.warning("Отправка не выполнена: %s", failure)
                # Курсор остаётся на месте: следующий такт повторит то же окно. Уже принятые
                # платформой записи будут отброшены ею как повторная доставка.
                return TickReport(
                    moment, start, end, len(readings), accepted, duplicates, str(failure)
                )
            accepted += result.accepted
            duplicates += result.duplicates

        if readings:
            self._platform_online = True
            self._platform_checked_at = _now()

        self._cursor = end
        self._store.save(SavedState(cursor=end))
        return TickReport(moment, start, end, len(readings), accepted, duplicates, None)

    # ─── Доступность платформы ────────────────────────────────────────────────

    async def _watch_platform(self) -> None:
        while True:
            self._platform_online = await self._platform.check()
            self._platform_checked_at = _now()
            await asyncio.sleep(_HEALTH_PERIOD_S)

    # ─── Состояние для веб-интерфейса ─────────────────────────────────────────

    def snapshot(self) -> dict[str, object]:
        report = self._last_tick
        return {
            "running": self._running,
            "exhausted": self._exhausted,
            "indexing": self._indexing,
            "cursor": self._cursor.isoformat(),
            "startDate": self._config.start_date.isoformat(),
            "intervalMinutes": self._interval_minutes,
            "stepMinutes": self._step_minutes,
            "speedup": round(self._step_minutes / self._interval_minutes, 3),
            "nextTickAt": self._next_tick_at.isoformat() if self._next_tick_at else None,
            "ticks": self._ticks,
            "acceptedTotal": self._accepted_total,
            "duplicatesTotal": self._duplicates_total,
            "platform": {
                "url": self._platform.base_url,
                "online": self._platform_online,
                "checkedAt": (
                    self._platform_checked_at.isoformat() if self._platform_checked_at else None
                ),
            },
            "lastTick": (
                None
                if report is None
                else {
                    "at": report.at.isoformat(),
                    "windowStart": report.window_start.isoformat(),
                    "windowEnd": report.window_end.isoformat(),
                    "readings": report.readings,
                    "accepted": report.accepted,
                    "duplicates": report.duplicates,
                    "error": report.error,
                }
            ),
            "sources": [
                {
                    "key": status.key,
                    "title": status.title,
                    "fileName": status.file_name,
                    "ready": status.ready,
                    "error": status.error,
                    "rowCount": status.row_count,
                    "sensorCount": status.sensor_count,
                    "firstTimestamp": (
                        status.first_timestamp.isoformat() if status.first_timestamp else None
                    ),
                    "lastTimestamp": (
                        status.last_timestamp.isoformat() if status.last_timestamp else None
                    ),
                }
                for status in self._sources.statuses()
            ],
        }


def _now() -> datetime:
    """Текущее реальное время без сведений о поясе.

    Отметки времени в источнике пояса не несут, и смешивать их с моментами реального времени
    в одних вычислениях нельзя. Здесь время нужно только для отсчёта интервалов, поэтому
    пояс отбрасывается сразу после получения времени в UTC.
    """
    return datetime.now(UTC).replace(tzinfo=None)
