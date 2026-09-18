"""Оценка числа токенов без токенизатора модели.

Точный подсчёт требует словаря конкретной модели, а у провайдеров модели разные и словари к
ним не публикуются вместе с API. Для показа заполненности контекста точность до токена не
нужна: достаточно порядка величины, поэтому число оценивается по составу текста.

Соотношения взяты из документации Yandex AI Studio, где один и тот же текст прогнан через
токенизаторы нескольких моделей: русский текст — 5,2 символа на токен у YandexGPT, 3,6 у Qwen3,
4,6 у gpt-oss; английский — 5,36 у Alice AI и 5,48 у Qwen3 и gpt-oss.

Отдельно считается то, чего в связном тексте мало: цифры, скопления знаков — разметка JSON и
кода — и символы прочих письменностей. Токенизаторы дробят их мельче слов, и общее соотношение
занизило бы оценку результатов инструментов, где JSON преобладает.
"""

import math
from dataclasses import dataclass, replace


@dataclass(frozen=True, slots=True)
class TokenizerProfile:
    name: str
    """Имя семейства для показа: по нему видно, какими соотношениями получена оценка."""
    cyrillic: float
    """Символов на токен в русском связном тексте."""
    latin: float
    """Символов на токен в английском связном тексте."""
    digits: float
    """Цифр на токен: Qwen делит числа поцифренно, словарь o200k — группами по три."""
    correction: float
    """Множитель к оценке, выведенный сверкой с числом токенов, которое сообщил провайдер."""


YANDEX = TokenizerProfile(name="yandex", cyrillic=5.2, latin=5.36, digits=2, correction=1)

# Поправка Qwen получена на 16 ходах `qwen3.6-35b-a3b` с журналами от 11 до 106 тысяч токенов:
# без неё оценка составляла 0,82–0,85 от `prompt_tokens` провайдера.
QWEN = TokenizerProfile(name="qwen", cyrillic=3.6, latin=5.48, digits=1, correction=1.17)

GPT_OSS = TokenizerProfile(name="gpt-oss", cyrillic=4.6, latin=5.48, digits=3, correction=1)

# Для модели неизвестного семейства взяты соотношения Qwen — наименьшие из измеренных:
# завышенная оценка заполненности безопаснее заниженной.
GENERIC = replace(QWEN, name="generic")

_SYMBOL_CHARS_PER_TOKEN = 2
"""Символов на токен в скоплениях знаков: `{"`, `":"`, `},` обычно занимают токен каждое."""

_PROSE_PUNCTUATION_SHARE = 0.08
"""Доля знаков препинания, свойственная связному тексту. Знаки сверх неё считаются разметкой."""


def tokenizer_for(model: str) -> TokenizerProfile:
    """Профиль по идентификатору модели: короткому имени или полному URI."""
    name = model.lower()
    if "yandexgpt" in name or "aliceai" in name:
        return YANDEX
    if "qwen" in name:
        return QWEN
    if "gpt-oss" in name:
        return GPT_OSS
    return GENERIC


def estimate_tokens(text: str, profile: TokenizerProfile = GENERIC) -> int:
    """Оценка числа токенов в тексте. Пустой текст токенов не занимает."""
    if text == "":
        return 0

    cyrillic = latin = digits = spaces = punctuation = other = 0
    for char in text:
        code = ord(char)
        if 0x61 <= code <= 0x7A or 0x41 <= code <= 0x5A:
            latin += 1
        elif 0x0400 <= code <= 0x04FF:
            cyrillic += 1
        elif 0x30 <= code <= 0x39:
            digits += 1
        elif code in (0x20, 0x0A, 0x09, 0x0D):
            spaces += 1
        elif _is_punctuation(code):
            punctuation += 1
        elif 0xC0 <= code <= 0x024F:
            latin += 1
        else:
            other += 1

    letters = cyrillic + latin
    prose_punctuation = min(punctuation, letters * _PROSE_PUNCTUATION_SHARE)
    prose = letters + spaces + prose_punctuation
    # Смешанный текст оценивается по долям письменностей: токены русской части и английской
    # складываются, а не усредняются соотношения.
    tokens_per_char = (
        1 / profile.latin
        if letters == 0
        else cyrillic / letters / profile.cyrillic + latin / letters / profile.latin
    )
    tokens = (
        prose * tokens_per_char
        + digits / profile.digits
        + (punctuation - prose_punctuation) / _SYMBOL_CHARS_PER_TOKEN
        # Иероглифы, эмодзи и прочие письменности занимают не меньше токена на символ.
        + other
    )
    return math.ceil(tokens * profile.correction)


def _is_punctuation(code: int) -> bool:
    """Знаки ASCII, типографские кавычки, тире и прочая общая пунктуация."""
    return (
        0x21 <= code <= 0x2F
        or 0x3A <= code <= 0x40
        or 0x5B <= code <= 0x60
        or 0x7B <= code <= 0x7E
        or code in (0xAB, 0xBB)
        or 0x2010 <= code <= 0x2027
    )
