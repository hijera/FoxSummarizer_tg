"""Главный файл запуска Telegram-бота."""
import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

from config import Config
from handlers import messages
from services.db import Database


# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def main():
    """Главная функция запуска бота."""
    # Валидация конфигурации
    try:
        Config.validate()
    except ValueError as e:
        logger.error(f"Ошибка конфигурации: {e}")
        return

    # Инициализация бота и диспетчера
    bot = Bot(
        token=Config.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    dp = Dispatcher()

    # Регистрация роутеров
    dp.include_router(messages.router)

    # Инициализация БД
    await Database.init()

    # Инициализация промптов (ленивая инициализация сервиса)
    summarizer_service = messages.get_summarizer_service()
    await summarizer_service.initialize_prompts()

    logger.info("Бот запущен и готов к работе")

    # Запуск бота
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await Database.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())

