"""Сохранение положения курсора между запусками.

Сохраняется только положение курсора. Признак работы не сохраняется намеренно: после запуска
и после перезапуска агрегатор всегда находится на паузе и поток данных сам не начинает.
"""

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SavedState:
    cursor: datetime


class StateStore:
    """Файл с положением курсора. Отказ записи не останавливает симуляцию."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> SavedState | None:
        """Читает сохранённое положение курсора. Отсутствующий или испорченный файл
        равнозначен отсутствию сохранения: курсор устанавливается на начальную дату."""
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            return SavedState(cursor=datetime.fromisoformat(raw["cursor"]))
        except FileNotFoundError:
            return None
        except (OSError, ValueError, KeyError, TypeError) as failure:
            logger.warning("Сохранённое состояние не прочитано: %s", failure)
            return None

    def save(self, state: SavedState) -> None:
        """Записывает положение курсора заменой файла целиком.

        Запись идёт во временный файл рядом и завершается переименованием: прерывание записи
        оставит прежнее сохранение, а не файл с половиной содержимого.
        """
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(self.path.suffix + ".tmp")
            temporary.write_text(
                json.dumps({"cursor": state.cursor.isoformat()}, ensure_ascii=False),
                encoding="utf-8",
            )
            os.replace(temporary, self.path)
        except OSError as failure:
            logger.warning("Состояние не сохранено: %s", failure)
