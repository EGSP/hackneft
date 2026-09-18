"""Базовая модель типов API ИИ-сервиса."""

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class ApiModel(BaseModel):
    """Базовая модель типов API ИИ-сервиса.

    Поля в Python именуются в snake_case, в JSON — в camelCase, как в API xip, откуда
    перенесён сервис. Разбор принимает оба варианта имени, сериализация по умолчанию выдаёт
    camelCase. Экземпляры неизменяемы: запрос и событие журнала после создания не правятся.
    """

    model_config = ConfigDict(
        alias_generator=to_camel,
        validate_by_name=True,
        validate_by_alias=True,
        serialize_by_alias=True,
        frozen=True,
    )
