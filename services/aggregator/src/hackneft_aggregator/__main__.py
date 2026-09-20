"""Запуск агрегатора: `uv run --package hackneft-aggregator hackneft-aggregator`."""

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
            "\nСкопируйте services/aggregator/.env.example в services/aggregator/.env "
            "и заполните значения.\n",
            file=sys.stderr,
        )
        raise SystemExit(1) from error

    # Сервис работает одним процессом: положение курсора и настройки темпа хранятся в памяти
    # процесса, поэтому нескольких рабочих процессов быть не может — они разошлись бы в
    # состоянии и отправляли бы одни и те же окна повторно.
    uvicorn.run(
        create_app(config),
        host=config.host,
        port=config.port,
        timeout_graceful_shutdown=5,
    )


if __name__ == "__main__":
    main()
