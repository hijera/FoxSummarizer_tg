"""Консольный скрипт для тестирования суммаризации сообщений из базы данных."""
import asyncio
import sys
from typing import List, Dict, Optional, Any
import aiosqlite
from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from services.summarizer import SummarizerService
from services.db import Database
from config import Config


def print_separator(char="=", length=80):
    """Печатает разделитель."""
    print(char * length)


def print_messages(messages: List[Dict]):
    """Печатает список сообщений в читаемом формате."""
    print("\n📨 ИСХОДНЫЕ СООБЩЕНИЯ:")
    print_separator("-")
    
    # Подсчитываем статистику по пользователям
    user_stats: Dict[str, Dict[str, Any]] = {}
    for msg in messages:
        username = msg.get("username", "")
        first_name = msg.get("first_name", "")
        last_name = msg.get("last_name", "")
        user_id = msg.get("user_id")
        
        # Формируем ключ пользователя
        user_key = f"{user_id}" if user_id else (username if username else first_name)
        if not user_key:
            user_key = "Неизвестный пользователь"
        
        # Формируем отображаемое имя
        name_parts = []
        if first_name:
            name_parts.append(first_name)
        if last_name:
            name_parts.append(last_name)
        full_name = " ".join(name_parts).strip()
        
        if full_name:
            display_name = full_name
            if username:
                display_name += f" (@{username})"
        elif username:
            display_name = f"@{username}"
        elif user_id:
            display_name = f"ID: {user_id}"
        else:
            display_name = "Неизвестный"
        
        if user_key not in user_stats:
            user_stats[user_key] = {
                "display_name": display_name,
                "count": 0
            }
        user_stats[user_key]["count"] += 1
    
    # Выводим сообщения
    for i, msg in enumerate(messages, 1):
        msg_id = msg.get("message_id", "N/A")
        text = msg.get("text", "")
        username = msg.get("username", "")
        first_name = msg.get("first_name", "")
        last_name = msg.get("last_name", "")
        user_id = msg.get("user_id", "")
        
        # Формируем имя автора
        name_parts = []
        if first_name:
            name_parts.append(first_name)
        if last_name:
            name_parts.append(last_name)
        full_name = " ".join(name_parts).strip()
        
        author = ""
        if full_name:
            author = full_name
        if username:
            author += f" (@{username})" if author else f"@{username}"
        if not author and user_id:
            author = f"ID: {user_id}"
        if not author:
            author = "Неизвестный пользователь"
        
        author_str = f" [{author}]" if author else ""
        print(f"{i}. [ID: {msg_id}]{author_str}")
        print(f"   {text[:200]}{'...' if len(text) > 200 else ''}")
        print()
    
    # Выводим статистику по пользователям
    if user_stats:
        print_separator("-")
        print("👥 СТАТИСТИКА ПО ПОЛЬЗОВАТЕЛЯМ:")
        sorted_users = sorted(user_stats.items(), key=lambda x: x[1]["count"], reverse=True)
        for user_key, stats in sorted_users:
            display_name = stats["display_name"]
            count = stats["count"]
            print(f"   {display_name}: {count} сообщени{'й' if count % 10 in [0, 5, 6, 7, 8, 9] or count % 100 in [11, 12, 13, 14] else 'я' if count % 10 == 1 else 'я'}")
        print_separator("-")


def print_summary(topics: List[Dict]):
    """Печатает результат суммаризации в читаемом формате."""
    print("\n📝 РЕЗУЛЬТАТ СУММАРИЗАЦИИ:")
    print_separator("=")
    
    if not topics:
        print("❌ Темы не найдены")
        return
    
    total_messages = sum(topic_item.get("message_count", 1) for topic_item in topics)
    
    for i, topic_item in enumerate(topics, 1):
        topic = topic_item.get("topic", "")
        topic_description = topic_item.get("topic_description", "")
        message_ids = topic_item.get("message_ids", [])
        message_count = topic_item.get("message_count", len(message_ids) if message_ids else 1)
        participants = topic_item.get("participants", [])
        
        print(f"\n🔹 Тема {i}: {topic}")
        if topic_description:
            print(f"   📄 Описание: {topic_description}")
        print(f"   💬 Сообщений в теме: {message_count}")
        
        if message_ids:
            ids_str = ", ".join(map(str, message_ids))
            print(f"   📌 Первое сообщение: [{ids_str}]")
        else:
            print("   📌 Сообщения: (не указаны)")
        
        # Выводим участников темы
        if participants:
            print(f"   👥 Участники ({len(participants)}):")
            for j, participant in enumerate(participants, 1):
                username = participant.get("username")
                first_name = participant.get("first_name")
                second_name = participant.get("second_name")
                p_message_count = participant.get("message_count", 0)
                
                # Формируем имя участника из новой структуры UserItem
                name_parts = []
                if first_name:
                    name_parts.append(first_name)
                if second_name:
                    name_parts.append(second_name)
                full_name = " ".join(name_parts).strip()
                
                if full_name:
                    participant_name = full_name
                    if username:
                        participant_name += f" (@{username})"
                elif username:
                    participant_name = f"@{username}"
                else:
                    participant_name = f"Участник {j}"
                
                print(f"      {j}. {participant_name} — {p_message_count} сообщени{'й' if p_message_count % 10 in [0, 5, 6, 7, 8, 9] or p_message_count % 100 in [11, 12, 13, 14] else 'е' if p_message_count % 10 == 1 else 'я'}")
        else:
            print("   👥 Участники: (не указаны)")
    
    print_separator("=")
    print(f"\n✅ Всего тем: {len(topics)}")
    print(f"📊 Всего сообщений в темах: {total_messages}")


async def get_chat_id_by_username(username: str, bot: Bot) -> Optional[int]:
    """
    Получает chat_id канала по его username через Telegram API.
    
    Args:
        username: Username канала (с @ или без)
        bot: Экземпляр бота для запросов к API
    
    Returns:
        chat_id канала или None, если не удалось найти
    """
    # Нормализуем username
    username = username.lstrip("@").strip()
    if not username:
        return None
    
    try:
        # Пробуем получить информацию о чате через get_chat
        chat = await bot.get_chat(f"@{username}")
        return chat.id
    except Exception as e:
        print(f"⚠️  Не удалось получить chat_id для @{username}: {e}")
        return None


async def resolve_chat_id(identifier: str, bot: Optional[Bot] = None) -> Optional[int]:
    """
    Определяет chat_id по переданному идентификатору (число или username).
    
    Args:
        identifier: ID канала (число) или username (строка)
        bot: Экземпляр бота для запросов к API (опционально)
    
    Returns:
        chat_id или None
    """
    # Пробуем интерпретировать как число
    try:
        chat_id = int(identifier)
        return chat_id
    except ValueError:
        pass
    
    # Если это строка (username), пытаемся получить через Bot API
    if bot:
        return await get_chat_id_by_username(identifier, bot)
    
    # Если бот не передан, пробуем найти в конфиге
    allowed_ids = Config.get_channel_ids()
    allowed_usernames = Config.get_channel_usernames()
    
    normalized = identifier.lstrip("@").lower()
    if normalized in allowed_usernames:
        # Если username есть в конфиге, но нет прямого маппинга,
        # возвращаем None и просим указать chat_id напрямую
        print(f"⚠️  Username @{identifier} найден в конфиге, но для получения chat_id нужен Bot API")
        return None
    
    return None


async def load_messages_from_db(chat_id: int) -> List[Dict]:
    """
    Загружает сообщения из базы данных для указанного канала.
    
    Args:
        chat_id: ID канала
    
    Returns:
        Список сообщений
    """
    messages = await Database.get_messages_for_chat(chat_id)
    return messages


async def list_available_chats():
    """Выводит список доступных чатов из базы данных."""
    assert Database._conn is not None, "Database is not initialized"
    Database._conn.row_factory = aiosqlite.Row
    
    async with Database._conn.execute(
        """
        SELECT DISTINCT chat_id, COUNT(*) as message_count
        FROM messages
        WHERE is_archived = 0
        GROUP BY chat_id
        ORDER BY chat_id
        """
    ) as cursor:
        rows = await cursor.fetchall()
    
    if not rows:
        print("❌ В базе данных нет сообщений")
        return
    
    print("\n📋 ДОСТУПНЫЕ ЧАТЫ В БАЗЕ ДАННЫХ:")
    print_separator("-")
    for row in rows:
        chat_id = row["chat_id"]
        count = row["message_count"]
        print(f"   Chat ID: {chat_id} ({count} сообщений)")
    print_separator("-")


async def main():
    """Главная функция."""
    print_separator("=")
    print("🧪 ТЕСТИРОВАНИЕ СУММАРИЗАЦИИ СООБЩЕНИЙ ИЗ БАЗЫ ДАННЫХ")
    print_separator("=")
    
    # Проверка конфигурации
    try:
        if not Config.OPENAI_API_KEY:
            print("❌ Ошибка: OPENAI_API_KEY не установлен в .env")
            print("   Убедитесь, что файл .env существует и содержит OPENAI_API_KEY")
            sys.exit(1)
    except Exception as e:
        print(f"❌ Ошибка конфигурации: {e}")
        sys.exit(1)
    
    # Инициализация базы данных
    print("\n⚙️  Инициализация базы данных...")
    try:
        await Database.init()
        print("✅ База данных инициализирована")
    except Exception as e:
        print(f"❌ Ошибка инициализации БД: {e}")
        sys.exit(1)
    
    # Определяем chat_id
    chat_id: Optional[int] = None
    
    if len(sys.argv) > 1:
        identifier = sys.argv[1]
        
        # Инициализируем бота для получения chat_id по username
        bot: Optional[Bot] = None
        if Config.BOT_TOKEN:
            try:
                bot = Bot(
                    token=Config.BOT_TOKEN,
                    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
                )
            except Exception as e:
                print(f"⚠️  Не удалось инициализировать бота: {e}")
                print("   Будет использоваться только числовой chat_id")
        
        chat_id = await resolve_chat_id(identifier, bot)
        
        if bot:
            await bot.session.close()
        
        if not chat_id:
            print(f"\n❌ Не удалось определить chat_id для: {identifier}")
            print("\n💡 Использование:")
            print("   python test_summarizer.py <chat_id>")
            print("   python test_summarizer.py <@username>")
            print("\n   Примеры:")
            print("   python test_summarizer.py -1001234567890")
            print("   python test_summarizer.py @channel_username")
            print("\n📋 Доступные чаты в БД:")
            await list_available_chats()
            await Database.close()
            sys.exit(1)
        
        print(f"\n✅ Используется Chat ID: {chat_id}")
    else:
        # Показываем список доступных чатов
        print("\n💡 Не указан chat_id или username")
        print("\n💡 Использование:")
        print("   python test_summarizer.py <chat_id>")
        print("   python test_summarizer.py <@username>")
        print("\n   Примеры:")
        print("   python test_summarizer.py -1001234567890")
        print("   python test_summarizer.py @channel_username")
        await list_available_chats()
        await Database.close()
        sys.exit(1)
    
    # Загружаем сообщения из БД
    print(f"\n📂 Загрузка сообщений из базы данных для chat_id={chat_id}...")
    messages = await load_messages_from_db(chat_id)
    
    if not messages:
        print(f"❌ Сообщения не найдены для chat_id={chat_id}")
        print("\n💡 Возможные причины:")
        print("   - В базе данных нет сообщений для этого канала")
        print("   - Все сообщения архивированы")
        print("   - Сообщения старше 24 часов (фильтруются автоматически)")
        await Database.close()
        sys.exit(1)
    
    print(f"✅ Загружено сообщений: {len(messages)}")
    
    # Выводим исходные сообщения
    print_messages(messages)
    
    # Инициализируем сервис
    print("\n⚙️  Инициализация сервиса суммаризации...")
    try:
        summarizer = SummarizerService()
        await summarizer.initialize_prompts()
        print("✅ Сервис инициализирован")
    except Exception as e:
        print(f"❌ Ошибка инициализации: {e}")
        await Database.close()
        sys.exit(1)
    
    # Выполняем суммаризацию
    print("\n🔄 Выполнение суммаризации...")
    print("   (это может занять некоторое время)")
    print_separator("-")
    
    try:
        topics = await summarizer.summarize(messages)
        print_separator("-")
        
        # Выводим результат
        print_summary(topics)
        
    except Exception as e:
        print(f"\n❌ Ошибка при суммаризации: {e}")
        import traceback
        traceback.print_exc()
        await Database.close()
        sys.exit(1)
    
    # Закрываем подключение к БД
    await Database.close()
    print("\n✅ Тестирование завершено")


if __name__ == "__main__":
    asyncio.run(main())

