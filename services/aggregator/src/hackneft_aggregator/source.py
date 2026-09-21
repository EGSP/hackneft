"""Чтение телеметрии и качества из файлов CSV.

Файлы телеметрии велики (236 и 87 МБ) и построчно упорядочены по времени, поэтому целиком в
память они не читаются. При запуске каждый файл прочитывается один раз, и от него остаётся
указатель: отметка времени каждой строки и смещение этой строки в байтах. Дальше окно по времени
находится двоичным поиском по указателю, а сами значения читаются с нужного смещения.

Выгрузки ЛИМС и ПАК после конвертации лежат в длинном формате (`date,tag,value`) и на два
порядка меньше телеметрии, поэтому читаются в память целиком и отдаются тем же интерфейсом окна.

Отметка времени ЛИМС — момент отбора пробы, а результат анализа появляется позже. Поэтому для
источника с задержкой окно отправки считается по моменту готовности результата (отметка плюс
задержка), а в запись уходит исходная отметка отбора: показание не отправляется раньше, чем
оно стало бы известно на установке, и на графике стоит в момент отбора.
"""

import csv
from bisect import bisect_left
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from math import isfinite
from pathlib import Path

from .catalog import (
    MISSING_VALUE_CODE,
    SOURCES,
    TAG_COLUMN,
    TIMESTAMP_COLUMN,
    VALUE_COLUMN,
    SourceSpec,
)


@dataclass(slots=True)
class Reading:
    """Одно показание, готовое к отправке."""

    timestamp: datetime
    sensor_code: str
    value: float
    source: str


@dataclass(slots=True)
class SourceStatus:
    """Состояние одного источника для веб-интерфейса."""

    key: str
    title: str
    file_name: str
    ready: bool
    error: str | None
    row_count: int
    sensor_count: int
    first_timestamp: datetime | None
    last_timestamp: datetime | None


class CsvSource:
    """Один файл телеметрии широкого формата: столбец отметки времени и столбец на каждый тег."""

    def __init__(self, spec: SourceSpec, path: Path) -> None:
        self.spec = spec
        self.path = path
        self.ready = False
        self.error: str | None = None
        self._timestamp_index = 0
        self._codes: dict[int, str] = {}
        """Номер столбца — код датчика с приставкой установки. Столбцы левее отметки времени
        и непригодные теги в отображение не входят и потому не читаются."""
        self._times: list[datetime] = []
        self._offsets: list[int] = []

    # ─── Указатель ────────────────────────────────────────────────────────────

    def build_index(self) -> None:
        """Прочитывает файл целиком и запоминает отметку времени и смещение каждой строки.

        Операция занимает несколько секунд на файл и выполняется однократно при запуске.
        Отказ не прерывает работу сервиса: источник остаётся неготовым, его состояние и
        причина отказа видны в веб-интерфейсе.
        """
        try:
            self._read_header()
            self._scan_rows()
        except OSError as failure:
            self.error = f"{type(failure).__name__}: {failure}"
            self.ready = False
            return
        except ValueError as failure:
            self.error = str(failure)
            self.ready = False
            return
        self.error = None
        self.ready = True

    def _read_header(self) -> None:
        with self.path.open("r", encoding="utf-8", newline="") as text:
            header = next(csv.reader(text))
        if TIMESTAMP_COLUMN not in header:
            raise ValueError(f"в заголовке нет столбца {TIMESTAMP_COLUMN}")
        self._timestamp_index = header.index(TIMESTAMP_COLUMN)
        self._codes = {
            position: self.spec.prefix + name.lower()
            for position, name in enumerate(header)
            if position > self._timestamp_index and name not in self.spec.excluded
        }

    def _scan_rows(self) -> None:
        times: list[datetime] = []
        offsets: list[int] = []
        with self.path.open("rb") as data:
            offset = len(data.readline())  # строка заголовка
            while True:
                line = data.readline()
                if not line:
                    break
                position = offset
                offset += len(line)
                stamp = _timestamp_of(line, self._timestamp_index)
                if stamp is None:
                    continue
                times.append(stamp)
                offsets.append(position)
        self._times = times
        self._offsets = offsets

    # ─── Состояние ────────────────────────────────────────────────────────────

    @property
    def first_timestamp(self) -> datetime | None:
        return self._times[0] if self._times else None

    @property
    def last_timestamp(self) -> datetime | None:
        return self._times[-1] if self._times else None

    def status(self) -> SourceStatus:
        return SourceStatus(
            key=self.spec.key,
            title=self.spec.title,
            file_name=self.spec.file_name,
            ready=self.ready,
            error=self.error,
            row_count=len(self._times),
            sensor_count=len(self._codes),
            first_timestamp=self.first_timestamp,
            last_timestamp=self.last_timestamp,
        )

    # ─── Чтение окна ──────────────────────────────────────────────────────────

    def read(self, start: datetime, end: datetime) -> Iterator[Reading]:
        """Возвращает показания с отметкой времени в полуинтервале [start, end).

        Пропускаются пустые ячейки, нечисловые значения, значения NaN и бесконечность,
        а также служебный код отсутствия данных 307.
        """
        if not self.ready:
            return
        position = bisect_left(self._times, start)
        if position >= len(self._times) or self._times[position] >= end:
            return
        with self.path.open("rb") as data:
            # Смещение берётся для каждой строки отдельно, а не подразумевается
            # последовательным чтением: в указатель попадают только строки с разобранной
            # отметкой времени, поэтому порядковый номер в указателе и порядок строк в файле
            # совпадают не обязательно.
            for number in range(position, len(self._times)):
                stamp = self._times[number]
                if stamp >= end:
                    return
                data.seek(self._offsets[number])
                yield from self._readings(stamp, data.readline())

    def _readings(self, stamp: datetime, line: bytes) -> Iterator[Reading]:
        cells = line.rstrip(b"\r\n").split(b",")
        for position, code in self._codes.items():
            if position >= len(cells):
                continue
            cell = cells[position]
            if not cell:
                continue
            try:
                value = float(cell)
            except ValueError:
                continue
            if not isfinite(value) or value == MISSING_VALUE_CODE:
                continue
            yield Reading(
                timestamp=stamp, sensor_code=code, value=value, source=self.spec.origin
            )


def _timestamp_of(line: bytes, index: int) -> datetime | None:
    """Разбирает отметку времени строки, не разбирая саму строку целиком."""
    cells = line.split(b",", index + 1)
    if len(cells) <= index:
        return None
    try:
        return datetime.fromisoformat(cells[index].decode("ascii").strip())
    except (ValueError, UnicodeDecodeError):
        return None


class LongCsvSource:
    """Длинный CSV качества: строка — одно показание (`date,tag,value`).

    Файл целиком помещается в память (сотни тысяч строк против миллионов у телеметрии). Окно
    по времени находится двоичным поиском по упорядоченному списку отметок.

    `release_delay` — задержка готовности показания относительно его отметки. Показание
    попадает в окно `[start, end)` по моменту `отметка + release_delay`, но отправляется с
    исходной отметкой. Первая и последняя отметки источника тоже сдвинуты на задержку:
    по последней из них исполнитель определяет, исчерпан ли источник.
    """

    def __init__(
        self, spec: SourceSpec, path: Path, release_delay: timedelta = timedelta(0)
    ) -> None:
        self.spec = spec
        self.path = path
        self.release_delay = release_delay
        self.ready = False
        self.error: str | None = None
        self._times: list[datetime] = []
        self._rows: list[list[Reading]] = []
        self._sensor_codes: set[str] = set()

    def build_index(self) -> None:
        try:
            self._load()
        except OSError as failure:
            self.error = f"{type(failure).__name__}: {failure}"
            self.ready = False
            return
        except ValueError as failure:
            self.error = str(failure)
            self.ready = False
            return
        self.error = None
        self.ready = True

    def _load(self) -> None:
        with self.path.open("r", encoding="utf-8", newline="") as text:
            reader = csv.DictReader(text)
            if reader.fieldnames is None:
                raise ValueError("пустой файл")
            required = {TIMESTAMP_COLUMN, TAG_COLUMN, VALUE_COLUMN}
            missing = required - set(reader.fieldnames)
            if missing:
                raise ValueError(
                    "в заголовке нет столбцов "
                    + ", ".join(sorted(missing))
                )

            buckets: dict[datetime, list[Reading]] = {}
            codes: set[str] = set()
            for row in reader:
                stamp = _parse_iso(row.get(TIMESTAMP_COLUMN, ""))
                if stamp is None:
                    continue
                tag = (row.get(TAG_COLUMN) or "").strip()
                if tag == "":
                    continue
                raw = (row.get(VALUE_COLUMN) or "").strip()
                if raw == "":
                    continue
                try:
                    value = float(raw)
                except ValueError:
                    continue
                if not isfinite(value):
                    continue
                code = self.spec.prefix + tag.lower()
                reading = Reading(
                    timestamp=stamp,
                    sensor_code=code,
                    value=value,
                    source=self.spec.origin,
                )
                buckets.setdefault(stamp, []).append(reading)
                codes.add(code)

        times = sorted(buckets)
        self._times = times
        self._rows = [buckets[stamp] for stamp in times]
        self._sensor_codes = codes

    @property
    def first_timestamp(self) -> datetime | None:
        return self._times[0] + self.release_delay if self._times else None

    @property
    def last_timestamp(self) -> datetime | None:
        return self._times[-1] + self.release_delay if self._times else None

    def status(self) -> SourceStatus:
        return SourceStatus(
            key=self.spec.key,
            title=self.spec.title,
            file_name=self.spec.file_name,
            ready=self.ready,
            error=self.error,
            row_count=sum(len(group) for group in self._rows),
            sensor_count=len(self._sensor_codes),
            first_timestamp=self.first_timestamp,
            last_timestamp=self.last_timestamp,
        )

    def read(self, start: datetime, end: datetime) -> Iterator[Reading]:
        if not self.ready:
            return
        # Окно по моменту готовности переводится в окно по отметке отбора.
        start, end = start - self.release_delay, end - self.release_delay
        position = bisect_left(self._times, start)
        for number in range(position, len(self._times)):
            stamp = self._times[number]
            if stamp >= end:
                return
            yield from self._rows[number]


def _parse_iso(raw: str) -> datetime | None:
    text = raw.strip()
    if text == "":
        return None
    try:
        return datetime.fromisoformat(text.replace(" ", "T", 1))
    except ValueError:
        return None


Source = CsvSource | LongCsvSource


@dataclass(slots=True)
class SourceSet:
    """Все источники вместе. Окно по времени читается из каждого из них."""

    sources: list[Source] = field(default_factory=list)

    @classmethod
    def from_directory(
        cls, directory: Path, release_delays: dict[str, timedelta] | None = None
    ) -> "SourceSet":
        """`release_delays` — задержка готовности показаний по ключу источника (см. LongCsvSource)."""
        delays = release_delays or {}
        items: list[Source] = []
        for spec in SOURCES:
            path = directory / spec.file_name
            if spec.layout == "long":
                items.append(LongCsvSource(spec, path, delays.get(spec.key, timedelta(0))))
            else:
                items.append(CsvSource(spec, path))
        return cls(items)

    def build_index(self) -> None:
        for source in self.sources:
            source.build_index()

    @property
    def ready(self) -> bool:
        return any(source.ready for source in self.sources)

    @property
    def last_timestamp(self) -> datetime | None:
        stamps = [s.last_timestamp for s in self.sources if s.last_timestamp is not None]
        return max(stamps) if stamps else None

    def read(self, start: datetime, end: datetime) -> list[Reading]:
        readings: list[Reading] = []
        for source in self.sources:
            readings.extend(source.read(start, end))
        readings.sort(key=lambda reading: (reading.timestamp, reading.sensor_code))
        return readings

    def statuses(self) -> list[SourceStatus]:
        return [source.status() for source in self.sources]
