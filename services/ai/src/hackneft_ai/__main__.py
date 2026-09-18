"""Запуск ИИ-сервиса: `uv run --package hackneft-ai hackneft-ai`."""

import io
import logging
import sys

import uvicorn

from .app import create_app
from .config import ConfigError, load_config, load_env_file


def main() -> None:
    # В Windows вывод, перенаправленный в файл, пишется в кодировке системы, и русские
    # сообщения о работе сервиса становятся нечитаемыми. Вывод переводится в UTF-8 явно.
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    load_env_file()
    try:
        config = load_config()
    except ConfigError as error:
        print("\nЗапуск невозможен: конфигурация неполна.\n", file=sys.stderr)
        for problem in error.problems:
            print(f"  • {problem}", file=sys.stderr)
        print(
            "\nСкопируйте services/ai/.env.example в services/ai/.env и заполните значения.\n",
            file=sys.stderr,
        )
        raise SystemExit(1) from error

    # Сервис работает одним процессом: ходы исполняются задачами в памяти процесса, а запись в
    # SQLite сериализуется блокировкой внутри него. Поэтому приложение передаётся объектом, без
    # перезапуска по изменениям и без нескольких рабочих процессов. Поток событий по SSE сам не
    # завершается, поэтому ожидание его закрытия при остановке ограничено.
    uvicorn.run(
        create_app(config),
        host=config.host,
        port=config.port,
        timeout_graceful_shutdown=5,
    )


if __name__ == "__main__":
    main()
