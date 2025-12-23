"""Скрипт для удаления сообщений старше заданного количества дней из SQLite-БД.

Запуск:
    python cleanup_old_messages.py

По умолчанию удаляет сообщения старше 30 дней.
Путь к БД берётся из Config.SQLITE_DB_PATH (см. config.py).
"""

import os
import sqlite3
import sys
import time
from pathlib import Path

from config import Config


def delete_old_messages(days: int = 30) -> int:
    """
    Удаляет сообщения старше `days` дней.

    Возвращает количество реально удалённых строк.
    """
    db_path = Path(Config.SQLITE_DB_PATH)
    if not db_path.exists():
        print(f"Файл БД не найден: {db_path.resolve()}")
        return 0

    # Timestamp `days` дней назад
    now_ts = int(time.time())
    threshold_ts = now_ts - days * 24 * 60 * 60

    connection = sqlite3.connect(str(db_path))
    try:
        cursor = connection.cursor()
        cursor.execute(
            "DELETE FROM messages WHERE date < ?",
            (threshold_ts,),
        )
        deleted_rows = cursor.rowcount if cursor.rowcount is not None else 0
        connection.commit()
    finally:
        connection.close()

    return deleted_rows


def main() -> None:
    """Точка входа скрипта."""
    days = 30
    if len(sys.argv) >= 2:
        try:
            days = int(sys.argv[1])
        except ValueError:
            print(
                f"Некорректное значение дней: {sys.argv[1]!r}. "
                f"Используется значение по умолчанию: {days}."
            )

    print(
        f"Удаление сообщений старше {days} дней "
        f"из БД по пути: {os.path.abspath(Config.SQLITE_DB_PATH)}"
    )
    deleted = delete_old_messages(days=days)
    print(f"Удалено строк: {deleted}")


if __name__ == "__main__":
    main()


