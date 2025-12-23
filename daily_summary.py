"""Скрипт для выполнения ежедневной суммаризации через cron."""
import asyncio
import logging
import sys
import os
import json
from pathlib import Path
from typing import Optional, Dict, List
from datetime import datetime, timezone

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

from config import Config
from services.db import Database
from services.summarizer import SummarizerService
from utils.chat_config import get_chat_settings, get_day_window_for_chat, get_daily_time_utc
from utils.formatter import format_summary_with_links


# Создаем директорию для логов, если её нет
logs_dir = Path('logs')
logs_dir.mkdir(exist_ok=True)

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('logs/daily_summary.log', encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)


async def perform_daily_summary_for_chat(
    bot: Bot,
    chat_id: int,
    chat_username: Optional[str],
    db_messages: List[Dict],
) -> bool:
    """
    Выполняет суммаризацию для одного чата и отправляет результат.
    Логика максимально повторяет обработчик команды /summarize.
    
    Args:
        bot: Экземпляр бота для отправки сообщений
        chat_id: ID чата
        chat_username: Username чата (опционально)
        db_messages: Сообщения из БД (уже загружены, БД закрыта)
    """
    if not db_messages:
        logger.info("DailySummarize.skip → chat_id=%s no_messages", chat_id)
        return False

    # Суммаризируем
    summarizer_service = SummarizerService()
    await summarizer_service.initialize_prompts()
    topics = await summarizer_service.summarize(db_messages, chat_id=chat_id, chat_username=chat_username)
    pre_filter_count = len(topics) if topics else 0
    per_topic_sizes = [t.get("message_count", 1) for t in (topics or [])]
    logger.info(
        "DailySummarize.topics ← chat_id=%s topics_count=%d",
        chat_id,
        pre_filter_count,
    )
    logger.debug(
        "DailySummarize.topics_sizes ← per_topic_message_count=%s",
        per_topic_sizes[:20],
    )

    # Применяем фильтры по настройкам чата (только «топовые» темы и ограничение количества тем)
    try:
        chat_settings = get_chat_settings(chat_id, chat_username)
        topics_cfg = (chat_settings.get("topics") or {})
        only_top = bool(topics_cfg.get("only_top", False))
        min_messages = int(topics_cfg.get("min_messages", 0) or 0)
        max_topics_raw = topics_cfg.get("max_topics", 0)
        max_topics = int(max_topics_raw or 0)
        logger.info(
            "DailySummarize.filter.config → chat_id=%s only_top=%s min_messages=%d max_topics=%d",
            chat_id,
            only_top,
            min_messages,
            max_topics,
        )

        if min_messages > 0 and topics:
            before = len(topics)
            topics = [
                t
                for t in topics
                if t.get("message_count", 1) >= min_messages
            ]
            after = len(topics)
            logger.info(
                "DailySummarize.filter.apply.min_messages → chat_id=%s before=%d after=%d",
                chat_id,
                before,
                after,
            )

        if max_topics > 0 and topics:
            before_limit = len(topics)
            topics = topics[:max_topics]
            after_limit = len(topics)
            logger.info(
                "DailySummarize.filter.apply.max_topics → chat_id=%s before=%d after=%d",
                chat_id,
                before_limit,
                after_limit,
            )
    except Exception as e:
        logger.exception(
            "DailySummarize.filter.error → chat_id=%s error=%s",
            chat_id,
            e,
        )

    if not topics:
        logger.warning(
            "DailySummarize.result → chat_id=%s empty topics after filtering (pre_filter_count=%d)",
            chat_id,
            pre_filter_count,
        )
        if pre_filter_count > 0:
            # Темы вернулись от LLM, но после фильтрации их не осталось
            try:
                await bot.send_message(
                    chat_id,
                    "Темы были найдены, но после фильтрации по настройкам чата не осталось ни одной темы."
                )
            except Exception as e:
                logger.exception(
                    "DailySummarize.notify.error → chat_id=%s error=%s",
                    chat_id,
                    e,
                )
        return False

    # Получаем настройки для отображения участников
    try:
        chat_settings = get_chat_settings(chat_id, chat_username)
        topics_cfg = (chat_settings.get("topics") or {})
        show_users = bool(topics_cfg.get("show_users", False))
        user_list_length = int(topics_cfg.get("user_list_length", 10) or 10)
        show_user_links = bool(topics_cfg.get("show_user_links", True))
    except Exception as e:
        logger.exception(
            "DailySummarize.config.error → chat_id=%s error=%s",
            chat_id,
            e,
        )
        show_users = False
        user_list_length = 10
        show_user_links = True

    # Форматируем результат (используем daily_summarize_template если задан)
    summary = await format_summary_with_links(
        topics, 
        chat_id, 
        messages=db_messages,
        show_users=show_users,
        user_list_length=user_list_length,
        chat_username=chat_username,
        show_user_links=show_user_links,
        use_daily_template=True
    )
    logger.info(
        "DailySummarize.format ← chat_id=%s summary_len=%d topics_final=%d show_users=%s",
        chat_id,
        len(summary or ""),
        len(topics),
        show_users,
    )

    # Отправляем суммаризацию
    try:
        await bot.send_message(chat_id, summary, parse_mode="HTML")
        logger.info("DailySummarize.send → chat_id=%s success", chat_id)
    except Exception as e:
        logger.exception(
            "DailySummarize.send.error → chat_id=%s error=%s",
            chat_id,
            e,
        )
        return False

    # После успешной отправки — очищаем хранилище (если нужно)
    # Архивация будет выполнена позже, после закрытия БД
    chat_settings = get_chat_settings(chat_id, chat_username)
    summarize_cfg = chat_settings.get("summarize") or {}
    no_clear = bool(summarize_cfg.get("no_clear_after_summarize", False))
    logger.info(
        "DailySummarize.clear.config → chat_id=%s username=%s no_clear_after_summarize=%s",
        chat_id,
        chat_username or "-",
        no_clear,
    )
    # Возвращаем информацию о необходимости архивации
    return not no_clear


async def main():
    """Главная функция для выполнения ежедневной суммаризации."""
    # Валидация конфигурации
    try:
        Config.validate()
    except ValueError as e:
        logger.error(f"Ошибка конфигурации: {e}")
        sys.exit(1)

    # Инициализация бота
    bot = Bot(
        token=Config.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )

    # Инициализация БД для быстрой загрузки данных
    try:
        await Database.init()
    except Exception as e:
        logger.exception(f"Ошибка инициализации БД: {e}")
        await bot.session.close()
        sys.exit(1)

    # Структура для хранения данных: {chat_id: {"messages": [...], "username": ..., "need_archive": bool}}
    chat_data: Dict[int, Dict] = {}
    chat_ids_to_process: List[int] = []

    try:
        # Получаем список всех чатов с сообщениями
        chat_ids = await Database.get_distinct_chat_ids()
        logger.info("DailySummarize.start → active_chats=%d", len(chat_ids))

        if not chat_ids:
            logger.info("DailySummarize.skip → no_active_chats")
            return

        # Быстро загружаем все данные из БД
        logger.info("DailySummarize.load → loading messages from DB...")
        for chat_id in chat_ids:
            # Получаем username чата (если возможно)
            chat_username: Optional[str] = None
            try:
                chat = await bot.get_chat(chat_id)
                chat_username = chat.username
            except Exception as e:
                logger.debug(
                    "DailySummarize.get_chat_failed → chat_id=%s error=%s",
                    chat_id,
                    e,
                )

            # Проверяем, включена ли ежедневная суммаризация для этого чата
            settings = get_chat_settings(chat_id, chat_username)
            summarize_cfg = settings.get("summarize") or {}
            daily_enabled = bool(summarize_cfg.get("daily_enabled", False))

            if not daily_enabled:
                logger.debug(
                    "DailySummarize.skip → chat_id=%s daily_enabled=false",
                    chat_id,
                )
                continue

            # Проверяем время запуска с учетом временной зоны
            daily_time_utc = get_daily_time_utc(chat_id, chat_username)
            if daily_time_utc is None:
                logger.debug(
                    "DailySummarize.skip_time → chat_id=%s username=%s invalid_time_config",
                    chat_id,
                    chat_username or "-",
                )
                continue

            # Получаем текущее время в UTC
            now_utc = datetime.now(timezone.utc)
            current_hour = now_utc.hour
            current_minute = now_utc.minute
            target_hour = daily_time_utc.hour
            target_minute = daily_time_utc.minute

            # Проверяем, совпадает ли текущее время с настройками (с точностью до минуты)
            if current_hour != target_hour or current_minute != target_minute:
                logger.debug(
                    "DailySummarize.skip_time → chat_id=%s current_utc=%02d:%02d target_utc=%02d:%02d",
                    chat_id,
                    current_hour,
                    current_minute,
                    target_hour,
                    target_minute,
                )
                continue

            # Проверяем, не запускали ли мы уже суммаризацию сегодня для этого чата
            last_run_file = Path("logs/last_daily_runs.json")
            last_runs: Dict[int, str] = {}
            if last_run_file.exists():
                try:
                    with open(last_run_file, "r", encoding="utf-8") as f:
                        last_runs = json.load(f)
                except Exception as e:
                    logger.warning("Failed to load last_runs file: %s", e)

            today_str = now_utc.date().isoformat()
            if last_runs.get(str(chat_id)) == today_str:
                logger.info(
                    "DailySummarize.skip_already_run → chat_id=%s date=%s",
                    chat_id,
                    today_str,
                )
                continue

            # Загружаем сообщения для этого чата
            try:
                day_window = get_day_window_for_chat(chat_id, chat_username)
            except Exception as e:
                logger.exception(
                    "DailySummarize.day_window.error → chat_id=%s username=%s error=%s",
                    chat_id,
                    chat_username or "-",
                    e,
                )
                day_window = None

            db_messages = None
            if day_window:
                date_from, date_to = day_window
                db_messages = await Database.get_messages_for_chat_in_range(chat_id, date_from, date_to)
                logger.info(
                    "DailySummarize.load → mode=day_window chat_id=%s date_from=%s date_to=%s db_messages_count=%d",
                    chat_id,
                    date_from,
                    date_to,
                    len(db_messages) if db_messages else 0,
                )
            else:
                db_messages = await Database.get_messages_for_chat(chat_id)
                logger.info(
                    "DailySummarize.load → mode=last_24h chat_id=%s db_messages_count=%d",
                    chat_id,
                    len(db_messages) if db_messages else 0,
                )

            if db_messages:
                # Получаем time_str из настроек для логирования
                time_str = (summarize_cfg.get("daily_time") or "23:00").strip()
                chat_data[chat_id] = {
                    "messages": db_messages,
                    "username": chat_username,
                    "time_str": time_str,
                }
                chat_ids_to_process.append(chat_id)

        # Закрываем БД сразу после загрузки всех данных
        await Database.close()
        logger.info("DailySummarize.load → DB closed, loaded %d chats", len(chat_ids_to_process))

        # Загружаем файл last_runs один раз перед обработкой всех чатов
        last_run_file = Path("logs/last_daily_runs.json")
        last_runs: Dict[str, str] = {}
        if last_run_file.exists():
            try:
                with open(last_run_file, "r", encoding="utf-8") as f:
                    last_runs = json.load(f)
            except Exception as e:
                logger.warning("Failed to load last_runs file: %s", e)
                last_runs = {}

        # Получаем текущую дату один раз
        now_utc = datetime.now(timezone.utc)
        today_str = now_utc.date().isoformat()

        # Теперь обрабатываем все данные без блокировки БД
        for chat_id in chat_ids_to_process:
            # Получаем username чата (если возможно), чтобы корректно применить настройки по username
            chat_username: Optional[str] = None
            try:
                chat = await bot.get_chat(chat_id)
                chat_username = chat.username
            except Exception as e:
                logger.debug(
                    "DailySummarize.get_chat_failed → chat_id=%s error=%s",
                    chat_id,
                    e,
                )

            # Проверяем, включена ли ежедневная суммаризация для этого чата
            settings = get_chat_settings(chat_id, chat_username)
            summarize_cfg = settings.get("summarize") or {}
            daily_enabled = bool(summarize_cfg.get("daily_enabled", False))

            if not daily_enabled:
                logger.debug(
                    "DailySummarize.skip → chat_id=%s daily_enabled=false",
                    chat_id,
                )
                continue

            # Проверяем время запуска с учетом временной зоны
            daily_time_utc = get_daily_time_utc(chat_id, chat_username)
            if daily_time_utc is None:
                logger.debug(
                    "DailySummarize.skip_time → chat_id=%s username=%s invalid_time_config",
                    chat_id,
                    chat_username or "-",
                )
                continue

            # Получаем текущее время в UTC (обновляем для каждого чата, так как время может измениться)
            current_now_utc = datetime.now(timezone.utc)
            current_hour = current_now_utc.hour
            current_minute = current_now_utc.minute
            target_hour = daily_time_utc.hour
            target_minute = daily_time_utc.minute

            # Проверяем, совпадает ли текущее время с настройками (с точностью до минуты)
            if current_hour != target_hour or current_minute != target_minute:
                logger.debug(
                    "DailySummarize.skip_time → chat_id=%s current_utc=%02d:%02d target_utc=%02d:%02d",
                    chat_id,
                    current_hour,
                    current_minute,
                    target_hour,
                    target_minute,
                )
                continue

            # Проверяем, не запускали ли мы уже суммаризацию сегодня для этого чата
            # Используем уже загруженный словарь last_runs и переменную today_str
            if last_runs.get(str(chat_id)) == today_str:
                logger.info(
                    "DailySummarize.skip_already_run → chat_id=%s date=%s",
                    chat_id,
                    today_str,
                )
                continue

            data = chat_data[chat_id]
            chat_username = data["username"]
            db_messages = data["messages"]
            time_str = data["time_str"]

            logger.info(
                "DailySummarize.run → chat_id=%s username=%s time=%s",
                chat_id,
                chat_username or "-",
                time_str,
            )

            try:
                need_archive = await perform_daily_summary_for_chat(
                    bot=bot,
                    chat_id=chat_id,
                    chat_username=chat_username,
                    db_messages=db_messages,
                )
                # Сохраняем информацию о необходимости архивации
                chat_data[chat_id]["need_archive"] = need_archive
                # Сохраняем дату последнего запуска в словарь (в памяти)
                last_runs[str(chat_id)] = today_str
                # Сохраняем файл после каждого успешного запуска, чтобы не потерять данные при ошибке
                try:
                    with open(last_run_file, "w", encoding="utf-8") as f:
                        json.dump(last_runs, f, indent=2)
                except Exception as e:
                    logger.warning("Failed to save last_runs file: %s", e)
            except Exception as e:
                logger.exception(
                    "DailySummarize.chat_error → chat_id=%s error=%s",
                    chat_id,
                    e,
                )

        # Архивируем сообщения для чатов, где это нужно (открываем БД снова только для архивации)
        chats_to_archive = [cid for cid in chat_ids_to_process if chat_data[cid].get("need_archive", False)]
        if chats_to_archive:
            logger.info("DailySummarize.archive → opening DB for archiving %d chats", len(chats_to_archive))
            try:
                await Database.init()
                for chat_id in chats_to_archive:
                    await Database.clear_chat(chat_id)
                    logger.info("DailySummarize.archive → chat_id=%s messages archived", chat_id)
            except Exception as e:
                logger.exception("DailySummarize.archive.error → error=%s", e)
            finally:
                await Database.close()
                logger.info("DailySummarize.archive → DB closed")

    finally:
        # Убеждаемся, что БД закрыта (на случай ошибки)
        try:
            await Database.close()
        except Exception:
            pass
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
