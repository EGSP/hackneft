import logging

logger = logging.getLogger(__name__)

# Известные теги датчиков (используются для определения типа данных)
KNOWN_SENSOR_TAGS = {
    "F1", "F2", "P3", "W4", "T5", "T6", "W7", "P8", "F9", "W10",
    "T11", "T12", "P13", "F14", "F15", "T16", "F17", "T18", "F19",
    "Q20", "Q21", "F22", "T23", "P24", "F25", "F26",
}


# Наблюдатель (Observer): реагирует на создание новой записи показания датчика
# и по sensor_tag определяет, что за данные пришли.


class SensorTagObserver:
    # Вызывается после сохранения записи в БД.
    # Пока выводит тег в консоль; далее сюда можно добавить сопоставление
    # тега из KNOWN_SENSOR_TAGS с типом данных (расход/давление/температура и т.д.)
    def notify(self, sensor_tag: str) -> None:
        print(sensor_tag)


# Синглтон-инстанс наблюдателя, используется в api.py
sensor_tag_observer = SensorTagObserver()
