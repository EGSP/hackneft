import logging

from hackneft_platform.events import SensorDataCreated, dispatcher

logger = logging.getLogger(__name__)


# Подписчик диспетчера: реагирует на создание любой записи показания датчика.
# Пока выводит код в консоль; далее сюда можно добавить сопоставление
# кода с типом данных (расход/давление/температура и т.д.)
@dispatcher.on()
async def log_sensor_code(event: SensorDataCreated) -> None:
    print(event.sensor_code)
