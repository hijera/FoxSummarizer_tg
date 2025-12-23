"""Сервис доступа к SQLite (асинхронный)."""
import os
from pathlib import Path
from typing import List, Dict, Optional
import aiosqlite
from config import Config


class Database:
    """Обертка над aiosqlite для хранения сообщений чата."""
    _conn: Optional[aiosqlite.Connection] = None
    _db_path: str = Config.SQLITE_DB_PATH

    @classmethod
    async def init(cls) -> None:
        """Инициализирует подключение и схему БД."""
        # Создаем директорию, если нужно
        db_path = Path(cls._db_path)
        if db_path.parent and not db_path.parent.exists():
            db_path.parent.mkdir(parents=True, exist_ok=True)

        cls._conn = await aiosqlite.connect(cls._db_path)
        await cls._conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                date INTEGER NOT NULL,
                text TEXT NOT NULL,
                username TEXT,
                user_id INTEGER,
                first_name TEXT,
                last_name TEXT,
                forward_id INTEGER,
                is_archived INTEGER NOT NULL DEFAULT 0,
                UNIQUE(chat_id, message_id)
            )
        """)
        await cls._conn.commit()
        # Оптимальные PRAGMA для простых записей/чтений
        await cls._conn.execute("PRAGMA journal_mode=WAL;")
        await cls._conn.execute("PRAGMA synchronous=NORMAL;")
        # Гарантируем наличие новых колонок при апдейте старой БД
        await cls._ensure_columns()

    @classmethod
    async def close(cls) -> None:
        """Закрывает подключение к БД."""
        if cls._conn is not None:
            await cls._conn.close()
            cls._conn = None

    @classmethod
    async def save_message(cls, chat_id: int, message_id: int, date_ts: int, text: str) -> None:
        """Сохраняет (вставляет/обновляет) сообщение в БД."""
        assert cls._conn is not None, "Database is not initialized"
        await cls._conn.execute(
            """
            INSERT INTO messages (chat_id, message_id, date, text, username, user_id, first_name, last_name, forward_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(chat_id, message_id) DO UPDATE SET
                date=excluded.date,
                text=excluded.text,
                username=excluded.username,
                user_id=excluded.user_id,
                first_name=excluded.first_name,
                last_name=excluded.last_name,
                forward_id=excluded.forward_id
            """,
            (chat_id, message_id, date_ts, text, None, None, None, None, None),
        )
        await cls._conn.commit()

    @classmethod
    async def get_messages_for_chat(cls, chat_id: int) -> List[Dict]:
        """Возвращает сообщения для чата за последние 24 часа, отсортированные по дате."""
        assert cls._conn is not None, "Database is not initialized"
        cls._conn.row_factory = aiosqlite.Row
        async with cls._conn.execute(
            """
            SELECT message_id, text, date, username, user_id, first_name, last_name, forward_id
            FROM messages
            WHERE chat_id = ?
              AND is_archived = 0
              AND date >= strftime('%s','now') - 86400
            ORDER BY date ASC
            """,
            (chat_id,),
        ) as cursor:
            rows = await cursor.fetchall()
            return [
                {
                    "message_id": row["message_id"],
                    "text": row["text"],
                    "date": row["date"],
                    "username": row["username"],
                    "user_id": row["user_id"],
                    "first_name": row["first_name"],
                    "last_name": row["last_name"],
                    "forward_id": row["forward_id"],
                }
                for row in rows
            ]

    @classmethod
    async def get_all_messages_for_chat(cls, chat_id: int) -> List[Dict]:
        """Возвращает все неархивированные сообщения для чата, отсортированные по дате."""
        assert cls._conn is not None, "Database is not initialized"
        cls._conn.row_factory = aiosqlite.Row
        async with cls._conn.execute(
            """
            SELECT message_id, text, date, username, user_id, first_name, last_name, forward_id
            FROM messages
            WHERE chat_id = ?
              AND is_archived = 0
            ORDER BY date ASC
            """,
            (chat_id,),
        ) as cursor:
            rows = await cursor.fetchall()
            return [
                {
                    "message_id": row["message_id"],
                    "text": row["text"],
                    "date": row["date"],
                    "username": row["username"],
                    "user_id": row["user_id"],
                    "first_name": row["first_name"],
                    "last_name": row["last_name"],
                    "forward_id": row["forward_id"],
                }
                for row in rows
            ]

    @classmethod
    async def get_messages_for_chat_in_range(cls, chat_id: int, date_from: int, date_to: int) -> List[Dict]:
        """
        Возвращает сообщения для чата за указанный интервал [date_from, date_to),
        отсортированные по дате.
        """
        assert cls._conn is not None, "Database is not initialized"
        cls._conn.row_factory = aiosqlite.Row
        async with cls._conn.execute(
            """
            SELECT message_id, text, date, username, user_id, first_name, last_name, forward_id
            FROM messages
            WHERE chat_id = ?
              AND is_archived = 0
              AND date >= ?
              AND date < ?
            ORDER BY date ASC
            """,
            (chat_id, date_from, date_to),
        ) as cursor:
            rows = await cursor.fetchall()
            return [
                {
                    "message_id": row["message_id"],
                    "text": row["text"],
                    "date": row["date"],
                    "username": row["username"],
                    "user_id": row["user_id"],
                    "first_name": row["first_name"],
                    "last_name": row["last_name"],
                    "forward_id": row["forward_id"],
                }
                for row in rows
            ]

    @classmethod
    async def get_distinct_chat_ids(cls) -> List[int]:
        """
        Возвращает список уникальных chat_id, для которых есть неархивированные сообщения.
        Используется планировщиком для ежедневной суммаризации.
        """
        assert cls._conn is not None, "Database is not initialized"
        cls._conn.row_factory = aiosqlite.Row
        async with cls._conn.execute(
            """
            SELECT DISTINCT chat_id
            FROM messages
            WHERE is_archived = 0
            """
        ) as cursor:
            rows = await cursor.fetchall()
            return [int(row["chat_id"]) for row in rows]

    @classmethod
    async def clear_chat(cls, chat_id: int) -> None:
        """Помечает все сообщения указанного чата как архивные (soft delete)."""
        assert cls._conn is not None, "Database is not initialized"
        await cls._conn.execute(
            "UPDATE messages SET is_archived = 1 WHERE chat_id = ? AND is_archived = 0",
            (chat_id,),
        )
        await cls._conn.commit()


    @classmethod
    async def save_message_with_username(
        cls,
        chat_id: int,
        message_id: int,
        date_ts: int,
        text: str,
        username: Optional[str],
    ) -> None:
        """Сохраняет сообщение, включая username, c UPSERT-логикой."""
        await cls.save_message_full(
            chat_id=chat_id,
            message_id=message_id,
            date_ts=date_ts,
            text=text,
            user_id=None,
            username=username,
            first_name=None,
            last_name=None,
            forward_id=None,
        )

    @classmethod
    async def save_message_full(
        cls,
        chat_id: int,
        message_id: int,
        date_ts: int,
        text: str,
        user_id: Optional[int],
        username: Optional[str],
        first_name: Optional[str],
        last_name: Optional[str],
        forward_id: Optional[int],
    ) -> None:
        """Сохраняет сообщение со всеми метаданными пользователя и связью (reply/forward)."""
        assert cls._conn is not None, "Database is not initialized"
        await cls._conn.execute(
            """
            INSERT INTO messages (chat_id, message_id, date, text, user_id, username, first_name, last_name, forward_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(chat_id, message_id) DO UPDATE SET
                date=excluded.date,
                text=excluded.text,
                user_id=excluded.user_id,
                username=excluded.username,
                first_name=excluded.first_name,
                last_name=excluded.last_name,
                forward_id=excluded.forward_id
            """,
            (chat_id, message_id, date_ts, text, user_id, username, first_name, last_name, forward_id),
        )
        await cls._conn.commit()

    @classmethod
    async def archive_messages_by_ids(cls, chat_id: int, message_ids: List[int]) -> None:
        """Помечает конкретные сообщения как архивные для переданного чата."""
        assert cls._conn is not None, "Database is not initialized"
        if not message_ids:
            return
        placeholders = ",".join("?" for _ in message_ids)
        params = [chat_id, *message_ids]
        await cls._conn.execute(
            f"UPDATE messages SET is_archived = 1 WHERE chat_id = ? AND message_id IN ({placeholders})",
            params,
        )
        await cls._conn.commit()

    @classmethod
    async def delete_messages_by_ids(cls, chat_id: int, message_ids: List[int]) -> None:
        """Безвозвратно удаляет сообщения по message_id для указанного чата."""
        assert cls._conn is not None, "Database is not initialized"
        if not message_ids:
            return
        placeholders = ",".join("?" for _ in message_ids)
        params = [chat_id, *message_ids]
        await cls._conn.execute(
            f"DELETE FROM messages WHERE chat_id = ? AND message_id IN ({placeholders})",
            params,
        )
        await cls._conn.commit()

    @classmethod
    async def _ensure_columns(cls) -> None:
        """Проверяет наличие новых колонок и добавляет их при необходимости."""
        assert cls._conn is not None, "Database is not initialized"
        cls._conn.row_factory = aiosqlite.Row
        async with cls._conn.execute("PRAGMA table_info(messages)") as cursor:
            rows = await cursor.fetchall()
        existing = {row["name"] for row in rows}
        if "username" not in existing:
            await cls._conn.execute("ALTER TABLE messages ADD COLUMN username TEXT")
            await cls._conn.commit()
        if "user_id" not in existing:
            await cls._conn.execute("ALTER TABLE messages ADD COLUMN user_id INTEGER")
            await cls._conn.commit()
        if "first_name" not in existing:
            await cls._conn.execute("ALTER TABLE messages ADD COLUMN first_name TEXT")
            await cls._conn.commit()
        if "last_name" not in existing:
            await cls._conn.execute("ALTER TABLE messages ADD COLUMN last_name TEXT")
            await cls._conn.commit()
        if "forward_id" not in existing:
            await cls._conn.execute("ALTER TABLE messages ADD COLUMN forward_id INTEGER")
            await cls._conn.commit()
        if "is_archived" not in existing:
            await cls._conn.execute("ALTER TABLE messages ADD COLUMN is_archived INTEGER NOT NULL DEFAULT 0")
            await cls._conn.commit()
