"""Чтение телеметрии из файлов CSV.

Файлы велики (236 и 87 МБ) и построчно упорядочены по времени, поэтому целиком в память они
не читаются. При запуске каждый файл прочитывается один раз, и от него остаётся указатель:
отметка времени каждой строки и смещение этой строки в байтах. Дальше окно по времени
находится двоичным поиском по указателю, а сами значения читаются с нужного смещения.

Указатель нужен потому, что курсор двигается не только вперёд: его можно сбросить к начальной
дате или перевести на произвольную дату из веб-интерфейса, и последовательного чтения от
текущего места для этого недостаточно.
"""

import csv
from bisect import bisect_left
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from math import isfinite
from pathlib import Path

from .catalog import MISSING_VALUE_CODE, SOURCES, TIMESTAMP_COLUMN, SourceSpec


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


@dataclass(slots=True)
class SourceSet:
    """Все источники телеметрии вместе. Окно по времени читается из каждого из них."""

    sources: list[CsvSource] = field(default_factory=list)

    @classmethod
    def from_directory(cls, directory: Path) -> "SourceSet":
        return cls([CsvSource(spec, directory / spec.file_name) for spec in SOURCES])

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
