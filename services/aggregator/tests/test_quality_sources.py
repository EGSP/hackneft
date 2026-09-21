"""Проверки длинного CSV качества и разбора шапки ЛИМС/ПАК."""

from datetime import datetime, timedelta
from pathlib import Path

from openpyxl import Workbook

from hackneft_aggregator.catalog import SOURCES
from hackneft_aggregator.quality_xlsx import (
    convert_lims_xlsx,
    convert_pak_xlsx,
    write_long_csv,
)
from hackneft_aggregator.source import LongCsvSource, SourceSet

SULFUR = "24-2000:Mg.Sulfur"
DENSITY = "24-2000:D15"


def _write_workbook(path: Path, rows: list[list[object]]) -> Path:
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    for row in rows:
        sheet.append(row)
    workbook.save(path)
    return path


def _pak_xlsx(directory: Path) -> Path:
    return _write_workbook(
        directory / "pak.xlsx",
        [
            [SULFUR, None, None, DENSITY, None],
            ["ppm", None, None, "кг/м3", None],
            [datetime(2023, 1, 1, 0, 0), 8.37, None, datetime(2025, 3, 5, 0, 0), 835.2],
            [datetime(2023, 1, 1, 0, 10), 8.41, None, None, None],
            [datetime(2023, 1, 1, 0, 20), 8.29, None, None, None],
        ],
    )


def _lims_xlsx(directory: Path) -> Path:
    """Шапка как в выданном пакете: установка и номер точки в кавычках."""
    ht = "Установка 'Гидроочистка'. Точка отбора '2'. продукт 'Дизельное топливо'"
    avt = "Установка 'АВТ'. Точка отбора '2.1'. продукт 'Дизельное топливо'"
    return _write_workbook(
        directory / "lims.xlsx",
        [
            [ht, None, None, None, avt, None],
            ["Mg.Sulfur", None, "FlashPoint", None, "D15", None],
            ["мг/кг", None, "°C", None, "кг/м3", None],
            [
                "Количество значений:",
                1462,
                "Количество значений:",
                1518,
                "Количество значений:",
                123,
            ],
            [datetime(2023, 1, 2, 10, 0), 8.6, datetime(2023, 1, 2, 10, 0), 68.0, None, None],
            [
                datetime(2023, 1, 3, 10, 0),
                11.6,
                None,
                None,
                datetime(2023, 1, 3, 9, 0),
                856.8,
            ],
        ],
    )


def test_pack_prefix_applied_from_long_csv(tmp_path: Path) -> None:
    readings = convert_pak_xlsx(_pak_xlsx(tmp_path))
    csv_path = tmp_path / "pack_tags.csv"
    write_long_csv(csv_path, readings)

    spec = next(item for item in SOURCES if item.key == "pack")
    source = LongCsvSource(spec, csv_path)
    source.build_index()

    assert source.ready
    window = source.read(datetime(2023, 1, 1), datetime(2026, 1, 1))
    codes = {reading.sensor_code for reading in window}
    assert codes == {"pack_24-2000:mg.sulfur", "pack_24-2000:d15"}


def test_lims_prefix_and_result_delay(tmp_path: Path) -> None:
    readings = convert_lims_xlsx(_lims_xlsx(tmp_path))
    csv_path = tmp_path / "lims_tags.csv"
    write_long_csv(csv_path, readings)

    # Время в CSV — момент отбора, как в выгрузке.
    assert {item.timestamp for item in readings} == {
        datetime(2023, 1, 2, 10, 0),
        datetime(2023, 1, 3, 10, 0),
        datetime(2023, 1, 3, 9, 0),
    }

    spec = next(item for item in SOURCES if item.key == "lims")
    source = LongCsvSource(spec, csv_path, release_delay=timedelta(hours=4))
    source.build_index()

    # Проба 10:00 попадает в окно готовности 14:00, но уходит с отметкой отбора.
    early = list(source.read(datetime(2023, 1, 2, 10, 0), datetime(2023, 1, 2, 14, 0)))
    assert early == []
    released = list(source.read(datetime(2023, 1, 2, 14, 0), datetime(2023, 1, 2, 14, 10)))
    assert {reading.timestamp for reading in released} == {datetime(2023, 1, 2, 10, 0)}
    codes = {
        reading.sensor_code
        for reading in source.read(datetime(2023, 1, 1), datetime(2026, 1, 1))
    }
    assert codes == {
        "lims_ht.2.mg.sulfur",
        "lims_ht.2.flashpoint",
        "lims_avt.2.1.d15",
    }


def test_source_set_skips_missing_quality_files(tmp_path: Path) -> None:
    """Отсутствие ЛИМС/ПАК не мешает готовой телеметрии."""
    (tmp_path / "avt_tags.csv").write_text(
        "Unnamed: 0,date,T1\n0,2023-01-01 00:00:00,100.0\n",
        encoding="utf-8",
    )
    (tmp_path / "242000_tags.csv").write_text(
        "Unnamed: 0,date,T6\n0,2023-01-01 00:00:00,200.0\n",
        encoding="utf-8",
    )

    sources = SourceSet.from_directory(tmp_path)
    sources.build_index()

    by_key = {status.key: status for status in sources.statuses()}
    assert by_key["avt"].ready
    assert by_key["ht"].ready
    assert not by_key["pack"].ready
    assert not by_key["lims"].ready
    assert sources.ready
