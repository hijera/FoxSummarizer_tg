"""Конфигурация бота из переменных окружения."""
import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    """Класс для хранения конфигурации бота."""
    
    # Telegram Bot
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
    # Чат для безопасной проверки существования сообщений (приватный чат/группа с ботом)
    TRASH_CHAT_ID: str = os.getenv("TRASH_CHAT_ID", "")
    
    # Канал(ы) для мониторинга (можно указать один или несколько через запятую)
    CHANNEL_ID: str = os.getenv("CHANNEL_ID", "")
    CHANNEL_USERNAME: str = os.getenv("CHANNEL_USERNAME", "")
    
    # OpenAI
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    OPENAI_BASE_URL: str = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    # Rate limiting / backoff
    OPENAI_MIN_DELAY_S: float = float(os.getenv("OPENAI_MIN_DELAY_S", "0.5"))
    OPENAI_MAX_RETRIES: int = int(os.getenv("OPENAI_MAX_RETRIES", "5"))
    OPENAI_BACKOFF_BASE_S: float = float(os.getenv("OPENAI_BACKOFF_BASE_S", "1.0"))
    OPENAI_BACKOFF_MAX_S: float = float(os.getenv("OPENAI_BACKOFF_MAX_S", "30.0"))

    # Summary / Topics
    SUMMARY_MAX_MESSAGE_IDS_PER_TOPIC: int = int(os.getenv("SUMMARY_MAX_MESSAGE_IDS_PER_TOPIC", "5"))
    
    # SQLite
    SQLITE_DB_PATH: str = os.getenv("SQLITE_DB_PATH", "data/messages.db")
    
    @classmethod
    def validate(cls) -> bool:
        """Проверка наличия обязательных параметров."""
        if not cls.BOT_TOKEN:
            raise ValueError("BOT_TOKEN не установлен в .env")
        if not cls.OPENAI_API_KEY:
            raise ValueError("OPENAI_API_KEY не установлен в .env")
        if not cls.CHANNEL_ID and not cls.CHANNEL_USERNAME:
            raise ValueError("CHANNEL_ID или CHANNEL_USERNAME должен быть установлен в .env")
        return True

    @classmethod
    def get_channel_ids(cls) -> set[int]:
        """
        Возвращает множество допустимых chat_id из CHANNEL_ID (поддерживает несколько через запятую).
        Некорректные значения игнорируются.
        """
        if not cls.CHANNEL_ID:
            return set()
        result: set[int] = set()
        for raw in cls.CHANNEL_ID.split(","):
            value = raw.strip()
            if not value:
                continue
            try:
                result.add(int(value))
            except ValueError:
                # игнорируем нечисловые значения
                continue
        return result

    @classmethod
    def get_channel_usernames(cls) -> set[str]:
        """
        Возвращает множество допустимых username из CHANNEL_USERNAME (поддерживает несколько через запятую).
        Username нормализуются: без '@' и в нижнем регистре.
        """
        if not cls.CHANNEL_USERNAME:
            return set()
        result: set[str] = set()
        for raw in cls.CHANNEL_USERNAME.split(","):
            handle = raw.strip()
            if not handle:
                continue
            if handle.startswith("@"):
                handle = handle[1:]
            result.add(handle.lower())
        return result

    @classmethod
    def get_trash_chat_id(cls) -> int | None:
        """
        Возвращает ID чата для тихой проверки существования сообщений.
        Должен быть приватный чат/группа, где бот может писать и удалять свои сообщения.
        """
        value = (cls.TRASH_CHAT_ID or "").strip()
        if not value:
            return None
        try:
            return int(value)
        except ValueError:
            return None

