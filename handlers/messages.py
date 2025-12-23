"""Обработчики сообщений для бота."""
from aiogram import Router, F
from aiogram.types import Message
from aiogram.enums import MessageEntityType
from aiogram.filters import Command
from typing import Dict, List, Optional, TYPE_CHECKING
from config import Config
from utils.formatter import format_summary_with_links
from services.db import Database
import logging
import re
from aiogram.exceptions import TelegramBadRequest
from utils.chat_config import get_chat_settings, get_day_window_for_chat, get_summary_window_for_chat, is_voice_recognition_enabled
from services.link_summarizer import LinkSummarizerService
if TYPE_CHECKING:
    from services.whisper_service import WhisperService
    from services.summarizer import SummarizerService


router = Router()

# Ленивая инициализация сервисов
_whisper_service: Optional['WhisperService'] = None
_summarizer_service: Optional['SummarizerService'] = None
_link_summarizer_service: Optional[LinkSummarizerService] = None

logger = logging.getLogger(__name__)


def get_whisper_service():
    """Получает экземпляр WhisperService (ленивая инициализация)."""
    global _whisper_service
    if _whisper_service is None:
        from services.whisper_service import WhisperService
        _whisper_service = WhisperService()
    return _whisper_service


def get_summarizer_service():
    """Получает экземпляр SummarizerService (ленивая инициализация)."""
    global _summarizer_service
    if _summarizer_service is None:
        from services.summarizer import SummarizerService
        _summarizer_service = SummarizerService()
    return _summarizer_service


def get_link_summarizer_service() -> LinkSummarizerService:
    """Получает экземпляр LinkSummarizerService (ленивая инициализация)."""
    global _link_summarizer_service
    if _link_summarizer_service is None:
        _link_summarizer_service = LinkSummarizerService()
    return _link_summarizer_service


def get_chat_identifier(chat_id: int, chat_username: str = None) -> bool:
    """
    Проверяет, является ли чат целевым каналом.
    
    Args:
        chat_id: ID чата
        chat_username: Username чата
    
    Returns:
        True если это целевой канал
    """
    # Списки ID и username из конфига
    allowed_ids = Config.get_channel_ids()
    allowed_usernames = Config.get_channel_usernames()

    if allowed_ids and chat_id in allowed_ids:
        return True

    if chat_username:
        normalized = chat_username.lstrip("@").lower()
        if allowed_usernames and normalized in allowed_usernames:
            return True

    return False


async def _filter_and_cleanup_deleted_messages(message: Message, chat_id: int, items: List[Dict]) -> List[Dict]:
    """
    Временная заглушка: не выполняет проверку существования сообщений.
    Возвращает элементы как есть без копирования и очистки.
    """
    return items


@router.message(F.text | F.caption)
async def handle_text_message(message: Message):
    """Обработчик текстовых сообщений."""
    chat_id = message.chat.id
    chat_username = message.chat.username
    raw_text = message.text or message.caption or ""

    # Фолбэк: ручное распознавание /summary|/summarize (с возможным @бот)
    if raw_text and re.match(r"^/(summary|summarize)(@\w+)?(\s|$)", raw_text.strip(), flags=re.IGNORECASE):
        logger.info("Fallback command detected in text: chat_id=%s message_id=%s", chat_id, message.message_id)
        return await handle_summarize_command(message)
    # Игнорируем команды, чтобы их обрабатывали командные хендлеры
    if message.entities and any(e.type == MessageEntityType.BOT_COMMAND for e in message.entities):
        logger.info("Skip text handler for bot_command: chat_id=%s message_id=%s", chat_id, message.message_id)
        return
    logger.info(
        "Incoming text message: chat_id=%s username=%s message_id=%s",
        chat_id,
        chat_username or "-",
        message.message_id,
    )
    
    # Проверяем, что это целевой канал
    if not get_chat_identifier(chat_id, chat_username):
        return
    
    # Получаем текст сообщения
    text = raw_text
    
    if not text.strip():
        return

    # Читаем настройки чата и параметры работы со ссылками
    try:
        chat_settings = get_chat_settings(chat_id, chat_username)
        links_summarize_enabled = bool(chat_settings.get("links_summarize", False))
        links_summarize_show_enabled = bool(chat_settings.get("links_summarize_show", False))
        youtube_summarize_enabled = bool(chat_settings.get("youtube_summarize", False))
        youtube_summarize_show_enabled = bool(chat_settings.get("youtube_summarize_show", False))
    except Exception as e:
        logger.exception(
            "TextMessage.chat_settings.error → chat_id=%s error=%s",
            chat_id,
            e,
        )
        links_summarize_enabled = False
        links_summarize_show_enabled = False
        youtube_summarize_enabled = False
        youtube_summarize_show_enabled = False

    # Обработка ссылок: суммаризация по содержимому и, опционально, вывод в чат
    suffix_for_storage = ""
    display_blocks = []
    if (
        (links_summarize_enabled or links_summarize_show_enabled)
        or (youtube_summarize_enabled or youtube_summarize_show_enabled)
    ) and text:
        link_svc = get_link_summarizer_service()
        suffix_for_storage, display_blocks = await link_svc.process_text(
            text,
            links_summarize=links_summarize_enabled,
            links_summarize_show=links_summarize_show_enabled,
            youtube_summarize=youtube_summarize_enabled,
            youtube_summarize_show=youtube_summarize_show_enabled,
            chat_id=chat_id,
            chat_username=chat_username,
        )
        logger.info(
            "TextMessage.links_processed → chat_id=%s "
            "links_summarize=%s links_summarize_show=%s "
            "youtube_summarize=%s youtube_summarize_show=%s "
            "suffix_len=%d display_blocks=%d",
            chat_id,
            links_summarize_enabled,
            links_summarize_show_enabled,
            youtube_summarize_enabled,
            youtube_summarize_show_enabled,
            len(suffix_for_storage or ""),
            len(display_blocks or []),
        )

    text_to_save = text + suffix_for_storage if suffix_for_storage else text

    # Сохраняем сообщение в БД (с добавленным пересказом ссылок, если включено)
    username = (message.from_user.username if message.from_user and message.from_user.username else chat_username)
    user_id = (message.from_user.id if message.from_user else None)
    first_name = (message.from_user.first_name if message.from_user else None)
    last_name = (message.from_user.last_name if message.from_user else None)
    forward_id = (message.reply_to_message.message_id if message.reply_to_message else getattr(message, "forward_from_message_id", None))
    await Database.save_message_full(
        chat_id=chat_id,
        message_id=message.message_id,
        date_ts=int(message.date.timestamp()),
        text=text_to_save,
        user_id=user_id,
        username=username,
        first_name=first_name,
        last_name=last_name,
        forward_id=forward_id,
    )

    # При включённом links_summarize_show выводим пересказ ссылок в чат
    if links_summarize_show_enabled and display_blocks:
        for block in display_blocks:
            if not block.strip():
                continue
            try:
                await message.reply(block, disable_web_page_preview=True)
            except TelegramBadRequest:
                # В некоторых типах чатов reply может быть недоступен — игнорируем
                logger.debug(
                    "TextMessage.links_reply_failed → chat_id=%s message_id=%s",
                    chat_id,
                    message.message_id,
                )


@router.message(F.voice | F.audio | F.video_note)
async def handle_audio_message(message: Message):
    """Обработчик аудиосообщений."""
    chat_id = message.chat.id
    chat_username = message.chat.username
    logger.info(
        "Incoming audio message: chat_id=%s username=%s message_id=%s",
        chat_id,
        chat_username or "-",
        message.message_id,
    )
    
    # Проверяем, что это целевой канал
    if not get_chat_identifier(chat_id, chat_username):
        return
    
    # Проверяем, включено ли распознавание голосовых сообщений
    if not is_voice_recognition_enabled(chat_id, chat_username):
        return
    
    # Определяем file_id в зависимости от типа
    if message.voice:
        file_id = message.voice.file_id
    elif message.audio:
        file_id = message.audio.file_id
    elif message.video_note:
        file_id = message.video_note.file_id
    else:
        return
    
    # Транскрибируем аудио
    whisper_service = get_whisper_service()
    transcribed_text = await whisper_service.download_and_transcribe(
        message.bot,
        file_id,
        chat_id
    )
    
    if transcribed_text:
        # Сохраняем транскрибированный текст как сообщение в БД
        username = (message.from_user.username if message.from_user and message.from_user.username else chat_username)
        user_id = (message.from_user.id if message.from_user else None)
        first_name = (message.from_user.first_name if message.from_user else None)
        last_name = (message.from_user.last_name if message.from_user else None)
        forward_id = (message.reply_to_message.message_id if message.reply_to_message else getattr(message, "forward_from_message_id", None))
        await Database.save_message_full(
            chat_id=chat_id,
            message_id=message.message_id,
            date_ts=int(message.date.timestamp()),
            text=transcribed_text,
            user_id=user_id,
            username=username,
            first_name=first_name,
            last_name=last_name,
            forward_id=forward_id,
        )


@router.channel_post(F.text | F.caption)
async def handle_channel_text_post(message: Message):
    """Обработчик текстовых сообщений из каналов (channel_post)."""
    chat_id = message.chat.id
    chat_username = message.chat.username
    raw_text = message.text or message.caption or ""

    # Фолбэк: ручное распознавание /summary|/summarize (с возможным @бот)
    if raw_text and re.match(r"^/(summary|summarize)(@\w+)?(\s|$)", raw_text.strip(), flags=re.IGNORECASE):
        logger.info("Fallback command detected in channel text: chat_id=%s message_id=%s", chat_id, message.message_id)
        return await handle_summarize_command(message)
    # Игнорируем команды, чтобы их обрабатывали командные хендлеры
    if message.entities and any(e.type == MessageEntityType.BOT_COMMAND for e in message.entities):
        logger.info("Skip channel text handler for bot_command: chat_id=%s message_id=%s", chat_id, message.message_id)
        return
    logger.info(
        "Incoming channel text: chat_id=%s username=%s message_id=%s",
        chat_id,
        chat_username or "-",
        message.message_id,
    )

    if not get_chat_identifier(chat_id, chat_username):
        return
    
    text = raw_text
    if not text.strip():
        return

    # Читаем настройки чата и параметры работы со ссылками
    try:
        chat_settings = get_chat_settings(chat_id, chat_username)
        links_summarize_enabled = bool(chat_settings.get("links_summarize", False))
        links_summarize_show_enabled = bool(chat_settings.get("links_summarize_show", False))
        youtube_summarize_enabled = bool(chat_settings.get("youtube_summarize", False))
        youtube_summarize_show_enabled = bool(chat_settings.get("youtube_summarize_show", False))
    except Exception as e:
        logger.exception(
            "ChannelText.chat_settings.error → chat_id=%s error=%s",
            chat_id,
            e,
        )
        links_summarize_enabled = False
        links_summarize_show_enabled = False
        youtube_summarize_enabled = False
        youtube_summarize_show_enabled = False

    # Обработка ссылок в постах канала
    suffix_for_storage = ""
    display_blocks = []
    if (
        (links_summarize_enabled or links_summarize_show_enabled)
        or (youtube_summarize_enabled or youtube_summarize_show_enabled)
    ) and text:
        link_svc = get_link_summarizer_service()
        suffix_for_storage, display_blocks = await link_svc.process_text(
            text,
            links_summarize=links_summarize_enabled,
            links_summarize_show=links_summarize_show_enabled,
            youtube_summarize=youtube_summarize_enabled,
            youtube__show=youtube_summarize_show_enabled,
        )
        logger.info(
            "ChannelText.links_processed → chat_id=%s "
            "links_summarize=%s links_summarize_show=%s "
            "youtube_summarize=%s youtube_summarize_show=%s "
            "suffix_len=%d display_blocks=%d",
            chat_id,
            links_summarize_enabled,
            links_summarize_show_enabled,
            youtube_summarize_enabled,
            youtube_summarize_show_enabled,
            len(suffix_for_storage or ""),
            len(display_blocks or []),
        )

    text_to_save = text + suffix_for_storage if suffix_for_storage else text

    username = (message.from_user.username if message.from_user and message.from_user.username else chat_username)
    user_id = (message.from_user.id if message.from_user else None)
    first_name = (message.from_user.first_name if message.from_user else None)
    last_name = (message.from_user.last_name if message.from_user else None)
    forward_id = (message.reply_to_message.message_id if message.reply_to_message else getattr(message, "forward_from_message_id", None))
    await Database.save_message_full(
        chat_id=chat_id,
        message_id=message.message_id,
        date_ts=int(message.date.timestamp()),
        text=text_to_save,
        user_id=user_id,
        username=username,
        first_name=first_name,
        last_name=last_name,
        forward_id=forward_id,
    )

    # При включённом links_summarize_show выводим пересказ ссылок в канал
    if links_summarize_show_enabled and display_blocks:
        for block in display_blocks:
            if not block.strip():
                continue
            try:
                await message.reply(block, disable_web_page_preview=True)
            except TelegramBadRequest:
                logger.debug(
                    "ChannelText.links_reply_failed → chat_id=%s message_id=%s",
                    chat_id,
                    message.message_id,
                )


@router.channel_post(F.voice | F.audio | F.video_note)
async def handle_channel_audio_post(message: Message):
    """Обработчик аудио/voice из каналов (channel_post)."""
    chat_id = message.chat.id
    chat_username = message.chat.username
    logger.info(
        "Incoming channel audio: chat_id=%s username=%s message_id=%s",
        chat_id,
        chat_username or "-",
        message.message_id,
    )

    if not get_chat_identifier(chat_id, chat_username):
        return

    # Проверяем, включено ли распознавание голосовых сообщений
    if not is_voice_recognition_enabled(chat_id, chat_username):
        return

    if message.voice:
        file_id = message.voice.file_id
    elif message.audio:
        file_id = message.audio.file_id
    elif message.video_note:
        file_id = message.video_note.file_id
    else:
        return

    whisper_service = get_whisper_service()
    transcribed_text = await whisper_service.download_and_transcribe(
        message.bot,
        file_id,
        chat_id
    )

    if transcribed_text:
        username = (message.from_user.username if message.from_user and message.from_user.username else chat_username)
        user_id = (message.from_user.id if message.from_user else None)
        first_name = (message.from_user.first_name if message.from_user else None)
        last_name = (message.from_user.last_name if message.from_user else None)
        forward_id = (message.reply_to_message.message_id if message.reply_to_message else getattr(message, "forward_from_message_id", None))
        await Database.save_message_full(
            chat_id=chat_id,
            message_id=message.message_id,
            date_ts=int(message.date.timestamp()),
            text=transcribed_text,
            user_id=user_id,
            username=username,
            first_name=first_name,
            last_name=last_name,
            forward_id=forward_id,
        )


@router.message(Command(commands={"summarize", "summary"}))
@router.channel_post(Command(commands={"summarize", "summary"}))
async def handle_summarize_command(message: Message):
    """Обработчик команд /summarize и /summary для создания суммаризации за последние 24 часа."""
    chat_id = message.chat.id
    chat_username = message.chat.username
    logger.info("Command summarize/summary received: chat_id=%s message_id=%s", chat_id, message.message_id)
    # Проверка настроек чата из config.yaml — можно отключить команду
    try:
        chat_settings = get_chat_settings(chat_id, chat_username)
        summarize_cfg = chat_settings.get("summarize") or {}
        command_enabled = bool(summarize_cfg.get("command_enabled", True))
        if not command_enabled:
            logger.info(
                "Summarize.command_disabled → chat_id=%s username=%s",
                chat_id,
                chat_username or "-",
            )
            try:
                await message.reply("Команда суммаризации в этом чате отключена настройками.")
            except TelegramBadRequest:
                # В некоторых типах чатов reply может быть недоступен — игнорируем
                pass
            return
    except Exception as e:
        logger.exception("Failed to read summarize.command_enabled from config.yaml: %s", e)
    
    # Получаем окно суммаризации из config.yaml (если задан summary_start_time)
    messages = None
    try:
        summary_window = get_summary_window_for_chat(chat_id, chat_username)
    except Exception as e:
        logger.exception(
            "Summarize.summary_window.error → chat_id=%s username=%s error=%s",
            chat_id,
            chat_username or "-",
            e,
        )
        summary_window = None

    if summary_window:
        # Если задан summary_start_time, используем временное окно
        date_from, date_to = summary_window
        messages = await Database.get_messages_for_chat_in_range(chat_id, date_from, date_to)
        logger.info(
            "Summarize.load → mode=summary_window chat_id=%s date_from=%s date_to=%s db_messages_count=%d",
            chat_id,
            date_from,
            date_to,
            len(messages) if messages else 0,
        )
    else:
        # Если параметр не задан, берем все сообщения (как раньше)
        messages = await Database.get_all_messages_for_chat(chat_id)
        logger.info(
            "Summarize.load → mode=all_messages chat_id=%s db_messages_count=%d",
            chat_id,
            len(messages) if messages else 0,
        )
    # Фильтруем удалённые в канале сообщения (если настроен TRASH_CHAT_ID)
    messages = await _filter_and_cleanup_deleted_messages(message, chat_id, messages)
    logger.info("Summarize.cleanup → after_cleanup_messages_count=%d", len(messages) if messages else 0)
    if not messages:
        await message.reply("Нет сообщений для суммаризации.")
        return
    
    # Суммаризируем
    await message.reply("Начинаю суммаризацию (может занимать до 5-10 минут)...")
    summarizer_service = get_summarizer_service()
    topics = await summarizer_service.summarize(messages, chat_id=chat_id, chat_username=chat_username)
    pre_filter_count = len(topics) if topics else 0
    per_topic_sizes = [t.get("message_count", 1) for t in (topics or [])]
    logger.info("Summarize.topics ← topics_count=%d", pre_filter_count)
    logger.debug("Summarize.topics_sizes ← per_topic_message_count=%s", per_topic_sizes[:20])

    # Применяем фильтры по настройкам чата (только «топовые» темы и ограничение количества тем)
    try:
        chat_settings = get_chat_settings(chat_id, chat_username)
        topics_cfg = (chat_settings.get("topics") or {})
        only_top = bool(topics_cfg.get("only_top", False))
        min_messages = int(topics_cfg.get("min_messages", 0) or 0)
        max_topics_raw = topics_cfg.get("max_topics", 0)
        max_topics = int(max_topics_raw or 0)
        logger.info(
            "Summarize.filter.config → only_top=%s min_messages=%d max_topics=%d",
            only_top,
            min_messages,
            max_topics,
        )

        if only_top and min_messages > 0 and topics:
            before = len(topics)
            topics = [
                t
                for t in topics
                if t.get("message_count", len(t.get("message_ids") or [])) >= min_messages
            ]
            after = len(topics)
            logger.info("Summarize.filter.apply.min_messages → before=%d after=%d", before, after)

        if max_topics > 0 and topics:
            before_limit = len(topics)
            topics = topics[:max_topics]
            after_limit = len(topics)
            logger.info("Summarize.filter.apply.max_topics → before=%d after=%d", before_limit, after_limit)
    except Exception as e:
        logger.exception("Failed to apply topics filter from config.yaml: %s", e)
    
    if not topics:
        logger.warning(
            "Summarize.result → empty topics after filtering (pre_filter_count=%d)",
            pre_filter_count
        )
        if pre_filter_count > 0:
            # Темы вернулись от LLM, но после фильтрации их не осталось
            try:
                await message.reply("Темы были найдены, но после фильтрации по настройкам чата не осталось ни одной темы.")
            except TelegramBadRequest:
                # В некоторых типах чатов reply может быть недоступен — игнорируем
                pass
        else:
            # Темы вообще не вернулись от LLM
            try:
                await message.reply("Не удалось создать суммаризацию.")
            except TelegramBadRequest:
                pass
        return
    
    # Получаем настройки для отображения участников
    try:
        chat_settings = get_chat_settings(chat_id, chat_username)
        topics_cfg = (chat_settings.get("topics") or {})
        show_users = bool(topics_cfg.get("show_users", False))
        user_list_length = int(topics_cfg.get("user_list_length", 10) or 10)
        show_user_links = bool(topics_cfg.get("show_user_links", True))
    except Exception as e:
        logger.exception("Failed to read show_users/user_list_length/show_user_links from config.yaml: %s", e)
        show_users = False
        user_list_length = 10
        show_user_links = True
    
    # Форматируем результат
    summary = await format_summary_with_links(
        topics, 
        chat_id, 
        messages=messages,
        show_users=show_users,
        user_list_length=user_list_length,
        chat_username=chat_username,
        show_user_links=show_user_links
    )
    logger.info("Summarize.format ← summary_len=%d topics_final=%d show_users=%s", len(summary or ""), len(topics), show_users)

    # Отправляем суммаризацию
    await message.reply(summary, parse_mode="HTML")
    
    # Очищаем хранилище после суммаризации (в БД)
    chat_settings = get_chat_settings(chat_id, chat_username)
    summarize_cfg = chat_settings.get("summarize") or {}
    no_clear = bool(summarize_cfg.get("no_clear_after_summarize", False))
    logger.info(
        "Summarize.clear.config → chat_id=%s username=%s no_clear_after_summarize=%s",
        chat_id,
        chat_username or "-",
        no_clear,
    )
    if not no_clear:
        await Database.clear_chat(chat_id)
        logger.info("Summarize.clear → chat_id=%s messages archived", chat_id)
    else:
        logger.info("Summarize.clear → chat_id=%s skipped (no_clear_after_summarize=true)", chat_id)

@router.message(Command(commands={"summarize_day", "summary_day"}))
@router.channel_post(Command(commands={"summarize_day", "summary_day"}))
async def handle_summarize_day_command(message: Message):
    """Обработчик команд /summarize_day и /summary_day для суммаризации за прошедший день."""
    chat_id = message.chat.id
    chat_username = message.chat.username
    logger.info("Command summarize_day/summary_day received: chat_id=%s message_id=%s", chat_id, message.message_id)
    # Проверка настроек чата из config.yaml — можно отключить команду
    try:
        chat_settings = get_chat_settings(chat_id, chat_username)
        summarize_cfg = chat_settings.get("summarize") or {}
        command_enabled = bool(summarize_cfg.get("command_enabled", True))
        if not command_enabled:
            logger.info(
                "SummarizeDay.command_disabled → chat_id=%s username=%s",
                chat_id,
                chat_username or "-",
            )
            try:
                await message.reply("Команда суммаризации в этом чате отключена настройками.")
            except TelegramBadRequest:
                # В некоторых типах чатов reply может быть недоступен — игнорируем
                pass
            return
    except Exception as e:
        logger.exception("Failed to read summarize.command_enabled from config.yaml (day): %s", e)

    # Получаем окно «прошедшего дня» из config.yaml
    messages = None
    try:
        day_window = get_day_window_for_chat(chat_id, chat_username)
    except Exception as e:
        logger.exception(
            "SummarizeDay.day_window.error → chat_id=%s username=%s error=%s",
            chat_id,
            chat_username or "-",
            e,
        )
        day_window = None

    if day_window:
        date_from, date_to = day_window
        messages = await Database.get_messages_for_chat_in_range(chat_id, date_from, date_to)
        logger.info(
            "SummarizeDay.load → mode=day_window chat_id=%s date_from=%s date_to=%s db_messages_count=%d",
            chat_id,
            date_from,
            date_to,
            len(messages) if messages else 0,
        )
    else:
        messages = await Database.get_messages_for_chat(chat_id)
        logger.info(
            "SummarizeDay.load → mode=last_24h_fallback chat_id=%s db_messages_count=%d",
            chat_id,
            len(messages) if messages else 0,
        )

    # Фильтруем удалённые в канале сообщения (если настроен TRASH_CHAT_ID)
    messages = await _filter_and_cleanup_deleted_messages(message, chat_id, messages)
    logger.info("SummarizeDay.cleanup → after_cleanup_messages_count=%d", len(messages) if messages else 0)
    if not messages:
        await message.reply("Нет сообщений для суммаризации.")
        return

    # Суммаризируем
    await message.reply("Начинаю суммаризацию за прошедший день (может занимать до 5-10 минут)...")
    summarizer_service = get_summarizer_service()
    topics = await summarizer_service.summarize(messages, chat_id=chat_id, chat_username=chat_username)
    pre_filter_count = len(topics) if topics else 0
    per_topic_sizes = [t.get("message_count", 1) for t in (topics or [])]
    logger.info("SummarizeDay.topics ← topics_count=%d", pre_filter_count)
    logger.debug("SummarizeDay.topics_sizes ← per_topic_message_count=%s", per_topic_sizes[:20])

    # Применяем фильтры по настройкам чата (только «топовые» темы и ограничение количества тем)
    try:
        chat_settings = get_chat_settings(chat_id, chat_username)
        topics_cfg = (chat_settings.get("topics") or {})
        only_top = bool(topics_cfg.get("only_top", False))
        min_messages = int(topics_cfg.get("min_messages", 0) or 0)
        max_topics_raw = topics_cfg.get("max_topics", 0)
        max_topics = int(max_topics_raw or 0)
        logger.info(
            "SummarizeDay.filter.config → only_top=%s min_messages=%d max_topics=%d",
            only_top,
            min_messages,
            max_topics,
        )

        if only_top and min_messages > 0 and topics:
            before = len(topics)
            topics = [
                t
                for t in topics
                if t.get("message_count", len(t.get("message_ids") or [])) >= min_messages
            ]
            after = len(topics)
            logger.info("SummarizeDay.filter.apply.min_messages → before=%d after=%d", before, after)

        if max_topics > 0 and topics:
            before_limit = len(topics)
            topics = topics[:max_topics]
            after_limit = len(topics)
            logger.info("SummarizeDay.filter.apply.max_topics → before=%d after=%d", before_limit, after_limit)
    except Exception as e:
        logger.exception("Failed to apply topics filter from config.yaml (day): %s", e)

    if not topics:
        logger.warning(
            "SummarizeDay.result → empty topics after filtering (pre_filter_count=%d)",
            pre_filter_count
        )
        if pre_filter_count > 0:
            # Темы вернулись от LLM, но после фильтрации их не осталось
            try:
                await message.reply("Темы были найдены, но после фильтрации по настройкам чата не осталось ни одной темы.")
            except TelegramBadRequest:
                # В некоторых типах чатов reply может быть недоступен — игнорируем
                pass
        else:
            # Темы вообще не вернулись от LLM
            try:
                await message.reply("Не удалось создать суммаризацию.")
            except TelegramBadRequest:
                pass
        return

    # Получаем настройки для отображения участников
    try:
        chat_settings = get_chat_settings(chat_id, chat_username)
        topics_cfg = (chat_settings.get("topics") or {})
        show_users = bool(topics_cfg.get("show_users", False))
        user_list_length = int(topics_cfg.get("user_list_length", 10) or 10)
        show_user_links = bool(topics_cfg.get("show_user_links", True))
    except Exception as e:
        logger.exception("Failed to read show_users/user_list_length/show_user_links from config.yaml (day): %s", e)
        show_users = False
        user_list_length = 10
        show_user_links = True
    
    # Форматируем результат
    summary = await format_summary_with_links(
        topics, 
        chat_id, 
        messages=messages,
        show_users=show_users,
        user_list_length=user_list_length,
        chat_username=chat_username,
        show_user_links=show_user_links
    )
    logger.info("SummarizeDay.format ← summary_len=%d topics_final=%d", len(summary or ""), len(topics))

    # Отправляем суммаризацию
    await message.reply(summary, parse_mode="HTML")
    chat_settings = get_chat_settings(chat_id, chat_username)
    summarize_cfg = chat_settings.get("summarize") or {}
    no_clear = bool(summarize_cfg.get("no_clear_after_summarize", False))
    logger.info(
        "SummarizeDay.clear.config → chat_id=%s username=%s no_clear_after_summarize=%s",
        chat_id,
        chat_username or "-",
        no_clear,
    )
    # Очищаем хранилище после суммаризации (в БД)
    if not no_clear:
        await Database.clear_chat(chat_id)
        logger.info("SummarizeDay.clear → chat_id=%s messages archived", chat_id)
    else:
        logger.info("SummarizeDay.clear → chat_id=%s skipped (no_clear_after_summarize=true)", chat_id)


@router.message(Command("clear"))
@router.channel_post(Command("clear"))
async def handle_clear_command(message: Message):
    """Обработчик команды /clear для очистки хранилища сообщений."""
    chat_id = message.chat.id
    
    await Database.clear_chat(chat_id)
    await message.reply("Хранилище сообщений очищено.")

