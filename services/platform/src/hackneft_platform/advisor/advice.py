"""Карточка совета: итог советника объектом по JSON Schema.

Схема отдаётся ИИ-сервису при создании сессии советника (поле resultSchema): сервис требует
от модели ответ строго по ней и проверяет его. Описания полей — часть указаний модели,
поэтому в них записаны правила заполнения, а не только смысл поля.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from hackneft_platform.catalog import SULFUR_LIMS_CODE, SULFUR_PAK_CODE

# Параметры, к которым может относиться действие: коды датчиков из исходных данных запуска.
# Перечень закрыт — советник данных не видит, и без него придумывает коды.
PARAMETERS = {
    "ht_t6": "температура на входе в реактор",
    "ht_p13": "давление на входе в реактор",
    "ht_f9": "подача сырья",
    "ht_f25": "расход водородсодержащего газа",
    "ht_f2": "расход газа",
    "ht_q20": "сера в сырье",
    SULFUR_PAK_CODE: "сера продукта по ПАК",
    SULFUR_LIMS_CODE: "сера продукта по ЛИМС",
}
Parameter = Literal[
    "ht_t6", "ht_p13", "ht_f9", "ht_f25", "ht_f2", "ht_q20", SULFUR_PAK_CODE, SULFUR_LIMS_CODE
]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


AdviceType = Literal["hold", "check", "adjust"]
"""Тип совета. Три значения и ничего сверх: при более дробной градации модель ставила
«предупреждение» совету «ничего не делать»."""


def normalize_card(card: dict[str, Any]) -> dict[str, Any]:
    """Карточка прежнего формата (decision и risk) в виде с типом совета.

    Советы, записанные до перехода на три типа, остаются в базе; интерфейс получает их уже
    с полем type, чтобы не различать версии.
    """
    if "type" in card:
        return card
    actions = card.get("actions") or []
    if card.get("decision") == "hold" or not actions:
        kind = "hold"
    elif any(action.get("type") == "adjust" for action in actions):
        kind = "adjust"
    else:
        kind = "check"
    rest = {key: value for key, value in card.items() if key not in ("decision", "risk")}
    return {"type": kind, **rest}


class AdviceValue(_Strict):
    value: float = Field(description="Число из исходных данных или обоснованная цель")
    unit: str = Field(max_length=20, description="Единица измерения: мг/кг, °C, т/ч, МПа, нм³/ч")


class AdviceAction(_Strict):
    type: Literal["adjust", "check"] = Field(
        description="adjust — изменить режим установки; check — проверить прибор, пробу или подачу"
    )
    parameter: Parameter = Field(
        description=(
            "Код параметра, к которому относится действие. "
            + "; ".join(f"{code} — {name}" for code, name in PARAMETERS.items())
        )
    )
    text: str = Field(
        max_length=90,
        description=(
            "Конкретное действие: что сделать, с каким параметром и с какими числами. "
            "Примеры: «Снизить подачу сырья с 201 до 185 т/ч»; «Отобрать внеочередную пробу "
            "ЛИМС: ПАК 9,99 мг/кг при пороге 10 мг/кг». Общие слова без параметра и числа "
            "(«Усилить контроль», «Проверить сырьё») недопустимы"
        ),
    )
    current: AdviceValue = Field(
        description="Последнее значение параметра из исходных данных, с единицей"
    )
    direction: Literal["increase", "decrease", "keep"] | None = Field(
        description="Направление изменения; null для проверки"
    )
    target: AdviceValue | None = Field(
        description=(
            "Целевое значение — только если оно следует из данных. Для проверки — значение, "
            "при котором проверка считается пройденной, либо null"
        )
    )


class AdviceCard(_Strict):
    type: AdviceType = Field(
        description=(
            "Тип совета. hold — режим не менять: сера ниже порога 10 мг/кг с запасом и не "
            "растёт, действий нет, actions пуст; check — режим пока не менять, но проверить "
            "конкретные приборы, пробы или подачу: признаки риска есть, а данных для изменения "
            "режима мало, actions — только проверки; adjust — изменить режим: превышение "
            "порога подтверждено или неизбежно без действий, actions содержит изменение режима"
        )
    )
    headline: str = Field(
        max_length=60,
        description=(
            "Главное одной фразой с параметром и числом: «Снизить подачу сырья до 185 т/ч», "
            "«Сверить ПАК 9,99 мг/кг с пробой ЛИМС», «Режим не менять: сера 6,2 мг/кг»"
        ),
    )
    actions: list[AdviceAction] = Field(
        max_length=3,
        description=(
            "Действия по приоритету. Несколько — только при реальной необходимости. "
            "При type = hold — пустой список"
        ),
    )
    because: str = Field(
        max_length=160,
        description=(
            "Факты, на которых основан совет: значения с единицами, изменение за период и "
            "время. Пример: «ПАК 9,99 мг/кг, +1,2 мг/кг за 30 мин; подача сырья 182 → 201 т/ч "
            "с 10:20». Без чисел не пиши"
        ),
    )
    protection: str = Field(
        max_length=80,
        description="Вывод агента защиты одной фразой с числами: что угрожает порогу и насколько",
    )
    production: str = Field(
        max_length=80,
        description="Вывод агента производства одной фразой с числами либо причина отказа",
    )
    expected_effect: str = Field(
        max_length=100,
        description=(
            "Какой показатель, до какого значения и к какому сроку должен измениться: "
            "«Сера ПАК ниже 9 мг/кг через 60 мин». При hold — какое значение подтвердит, "
            "что режим можно не менять"
        ),
    )
    recheck_after_minutes: int | None = Field(
        ge=5, le=1440, description="Через сколько минут данных оценить эффект; null — не нужно"
    )
    confidence: Literal["high", "medium", "low"] = Field(description="Уверенность в выводе")

    @model_validator(mode="after")
    def _type_matches_actions(self) -> "AdviceCard":
        # Схема JSON согласованности типа и действий не выражает, поэтому тип сверяется с
        # действиями: при «режим не менять» действия отбрасываются, без действий совет не
        # может требовать проверки, а изменение режима означает тип adjust.
        if self.type == "hold":
            self.actions = []
        elif not self.actions:
            self.type = "hold"
        elif any(action.type == "adjust" for action in self.actions):
            self.type = "adjust"
        else:
            self.type = "check"
        return self


def _inline(schema: Any, defs: dict[str, Any]) -> Any:
    """Подставляет $defs на место ссылок и убирает заголовки.

    Формат ответа провайдеров поддерживает ссылки не везде, а заголовки pydantic («Headline»)
    только расходуют контекст модели.
    """
    if isinstance(schema, dict):
        if "$ref" in schema:
            return _inline(defs[schema["$ref"].rsplit("/", 1)[-1]], defs)
        return {
            key: _inline(value, defs)
            for key, value in schema.items()
            if key not in ("title", "$defs")
        }
    if isinstance(schema, list):
        return [_inline(item, defs) for item in schema]
    return schema


def advice_schema() -> dict[str, Any]:
    """JSON Schema карточки для ИИ-сервиса: все поля обязательны, лишние запрещены."""
    raw = AdviceCard.model_json_schema()
    schema: dict[str, Any] = _inline(raw, raw.get("$defs", {}))
    return schema


ADVICE_SCHEMA = advice_schema()
