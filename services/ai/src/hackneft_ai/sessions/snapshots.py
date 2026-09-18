"""Хранилище снимков постоянной части запроса к модели.

Идентификатор снимка — хеш его содержимого. Набор инструментов меняется редко, и все ходы с
одинаковым промптом и набором ссылаются на одну запись: описания инструментов занимают
десятки килобайт, и повторять их на каждом шаге значило бы раздувать базу без пользы.
"""

import hashlib
import json

from sqlalchemy.dialects.sqlite import insert

from hackneft_common.ai import RequestSnapshot, RequestSnapshotContent

from ..db.database import Database
from ..db.schema import RequestSnapshotRow, iso


class RequestSnapshotStore:
    """Реализация зависимости ядра `RequestSnapshots` и чтение снимка по идентификатору."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def save(self, snapshot: RequestSnapshotContent) -> str:
        content = snapshot.model_dump(mode="json")
        # Ключи упорядочиваются: порядок ключей в описаниях инструментов зависит от того,
        # откуда описание получено, а хеш должен зависеть только от содержимого.
        canonical = json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        snapshot_id = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        # Вставка без отказа при совпадении: два хода с одинаковым набором могут сохранять
        # снимок одновременно, и второй должен получить тот же идентификатор.
        async with self._db.write() as tx:
            await tx.execute(
                insert(RequestSnapshotRow)
                .values(
                    id=snapshot_id,
                    prompt=content["prompt"],
                    sections=content["sections"],
                    tools=content["tools"],
                )
                .on_conflict_do_nothing(index_elements=[RequestSnapshotRow.id])
            )
        return snapshot_id

    async def find(self, snapshot_id: str) -> RequestSnapshot | None:
        async with self._db.read() as session:
            row = await session.get(RequestSnapshotRow, snapshot_id)
            if row is None:
                return None
            # Содержимое проверяется схемой: поля JSON база не типизирует.
            return RequestSnapshot.model_validate(
                {
                    "id": row.id,
                    "prompt": row.prompt,
                    "sections": row.sections,
                    "tools": row.tools,
                    "created_at": iso(row.created_at),
                }
            )
