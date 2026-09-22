"""Конвертация выгрузок ЛИМС и ПАК из xlsx в длинный CSV для агрегатора.

Использование из корня репозитория (после `uv sync`):

    uv run --package hackneft-aggregator hackneft-convert-quality --data-dir ../data

По умолчанию ищет xlsx в каталоге данных и в его родителе (корень выданного пакета),
пишет `lims_tags.csv` и `pack_tags.csv` в `--data-dir`.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .quality_xlsx import convert_lims_xlsx, convert_pak_xlsx, write_long_csv

logger = logging.getLogger(__name__)

_PAK_NAMES = (
    "Выгрузка ПАК 01.01.2023 - н.в_.xlsx",
    "pak.xlsx",
)
_LIMS_NAMES = (
    "ЛИМСы 01.01.2023 - н.в_ (2).xlsx",
    "lims.xlsx",
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir",
        type=Path,
        required=True,
        help="Каталог, куда писать CSV (обычно ../data относительно репозитория)",
    )
    parser.add_argument(
        "--package-dir",
        type=Path,
        default=None,
        help="Корень пакета с xlsx; по умолчанию — data-dir и его родитель",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    search = [args.data_dir]
    if args.package_dir is not None:
        search.insert(0, args.package_dir)
    else:
        search.append(args.data_dir.parent)

    pak = _find(search, _PAK_NAMES)
    lims = _find(search, _LIMS_NAMES)
    if pak is None and lims is None:
        logger.error("не найдены файлы ПАК и ЛИМС в %s", ", ".join(str(p) for p in search))
        return 1

    args.data_dir.mkdir(parents=True, exist_ok=True)

    if pak is not None:
        readings = convert_pak_xlsx(pak)
        out = args.data_dir / "pack_tags.csv"
        write_long_csv(out, readings)
        logger.info("ПАК: %s → %s (%s записей)", pak.name, out, len(readings))
    else:
        logger.warning("файл ПАК не найден, pack_tags.csv не создан")

    if lims is not None:
        readings = convert_lims_xlsx(lims)
        out = args.data_dir / "lims_tags.csv"
        write_long_csv(out, readings)
        logger.info("ЛИМС: %s → %s (%s записей)", lims.name, out, len(readings))
    else:
        logger.warning("файл ЛИМС не найден, lims_tags.csv не создан")

    return 0


def _find(directories: list[Path], names: tuple[str, ...]) -> Path | None:
    for directory in directories:
        for name in names:
            candidate = directory / name
            if candidate.is_file():
                return candidate
    return None


if __name__ == "__main__":
    sys.exit(main())
