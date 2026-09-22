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


class AdviceTarget(_Strict):
    value: float = Field(description="Целевое значение параметра")
    unit: str = Field(max_length=20, description="Единица измерения: °C, т/ч, МПа, нм³/ч")


class AdviceAction(_Strict):
    type: Literal["adjust", "check"] = Field(
        description="adjust — изменить режим установки; check — проверить прибор, подачу или данные"
    )
    text: str = Field(
        max_length=60,
        description="Действие в повелительном наклонении, коротко: «Снизить подачу сырья»",
    )
    parameter: Parameter | None = Field(
        description=(
            "Код параметра, к которому относится действие; null, если ни к одному. "
            + "; ".join(f"{code} — {name}" for code, name in PARAMETERS.items())
        )
    )
    direction: Literal["increase", "decrease", "keep"] | None = Field(
        description="Направление изменения; null для проверки"
    )
    target: AdviceTarget | None = Field(
        description=(
            "Числовая цель — только если точно известно, до какого значения менять. "
            "Если значение не обосновано данными, null"
        )
    )


class AdviceCard(_Strict):
    decision: Literal["act", "hold"] = Field(
        description=(
            "act — оператору нужно действовать, actions не пуст; hold — ничего не делать, "
            "actions пуст"
        )
    )
    headline: str = Field(
        max_length=50,
        description="Главное одной фразой: «Снизить подачу сырья» или «Ничего не делать»",
    )
    risk: Literal["none", "watch", "warning", "critical"] = Field(
        description=(
            "Риск выпуска топлива вне нормы 10 мг/кг. "
            "none — сера в норме с запасом, признаков ухудшения нет; "
            "watch — сера в норме, но изменились сырьё, режим, газ или данные, и это может "
            "повлиять на качество; "
            "warning — сера устойчиво близка к норме (не ниже раннего порога 8 мг/кг) или "
            "растёт к норме; "
            "critical — превышение 10 мг/кг подтверждено ПАК или ЛИМС либо неизбежно без "
            "действий"
        )
    )
    actions: list[AdviceAction] = Field(
        max_length=3,
        description=(
            "Действия по приоритету. Несколько — только при реальной необходимости. "
            "При decision = hold — пустой список"
        ),
    )
    because: str = Field(max_length=120, description="Почему — одна короткая фраза")
    protection: str = Field(max_length=50, description="Вывод агента защиты одной фразой")
    production: str = Field(max_length=50, description="Вывод агента производства одной фразой")
    expected_effect: str = Field(
        max_length=80, description="Что должно измениться и по какому показателю это видно"
    )
    recheck_after_minutes: int | None = Field(
        ge=5, le=1440, description="Через сколько минут данных оценить эффект; null — не нужно"
    )
    confidence: Literal["high", "medium", "low"] = Field(description="Уверенность в выводе")

    @model_validator(mode="after")
    def _hold_has_no_actions(self) -> "AdviceCard":
        # Схема JSON этого не выражает: при «ничего не делать» действия отбрасываются.
        if self.decision == "hold":
            self.actions = []
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
