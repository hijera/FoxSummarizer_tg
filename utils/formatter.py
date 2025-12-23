"""Утилиты для форматирования результатов."""
from typing import List, Dict, Optional, Tuple
from config import Config
import logging
import re
from pathlib import Path
import aiofiles
from jinja2 import Template
from utils.chat_config import get_chat_settings


logger = logging.getLogger(__name__)


def format_message_link(chat_id: int, message_id: int) -> str:
    """
    Форматирует ссылку на сообщение в Telegram.
    
    Args:
        chat_id: ID чата
        message_id: ID сообщения
    
    Returns:
        Ссылка на сообщение в формате Telegram
    """
    # Преобразуем chat_id в строку для формирования ссылки
    # Для супергрупп и каналов убираем префикс -100
    chat_id_str = str(chat_id)
    if chat_id_str.startswith("-100"):
        chat_id_str = chat_id_str[4:]
    elif chat_id_str.startswith("-"):
        chat_id_str = chat_id_str[1:]
    
    return f"https://t.me/c/{chat_id_str}/{message_id}"


def _build_users_dict(messages: List[Dict]) -> Tuple[Dict[int, Dict[str, Optional[str]]], Dict[str, int]]:
    """
    Строит словарь user_id -> {username, first_name, last_name} из списка сообщений.
    Также строит словарь username -> user_id для обратного поиска.
    
    Args:
        messages: Список сообщений с полями user_id, username, first_name, last_name
    
    Returns:
        Кортеж (users_dict, username_to_user_id):
        - users_dict: {user_id: {username, first_name, last_name}}
        - username_to_user_id: {username_lower: user_id} для обратного поиска
    """
    users_dict = {}
    username_to_user_id = {}  # Для обратного поиска по username
    
    for msg in messages:
        user_id = msg.get("user_id")
        username = msg.get("username")
        first_name = msg.get("first_name")
        last_name = msg.get("last_name")
        
        # Если есть user_id - добавляем в основной словарь
        if user_id is not None:
            if user_id not in users_dict:
                users_dict[user_id] = {
                    "username": username,
                    "first_name": first_name,
                    "last_name": last_name,
                }
        
        # Сохраняем связь username -> user_id для обратного поиска
        if username and user_id is not None:
            clean_username = username.lstrip("@").lower()
            if clean_username:
                username_to_user_id[clean_username] = user_id
    
    return users_dict, username_to_user_id


def _format_user_name(user_info: Dict[str, Optional[str]], user_id: int, show_user_links: bool = True) -> Optional[str]:
    """
    Форматирует имя пользователя для отображения.
    
    Args:
        user_info: Словарь с username, first_name, last_name
        user_id: ID пользователя (для логирования)
        show_user_links: Если True, создает ссылку на профиль при наличии username
    
    Returns:
        Отформатированное имя или None если нет данных
    """
    username = user_info.get("username")
    first_name = user_info.get("first_name")
    last_name = user_info.get("last_name")
    
    # Формируем полное имя
    name_parts = []
    if first_name:
        name_parts.append(first_name)
    if last_name:
        name_parts.append(last_name)
    full_name = " ".join(name_parts).strip()
    
    # Если есть username и включены ссылки - возвращаем ссылку с полным именем
    if username and show_user_links:
        # Убираем @ из username если есть
        clean_username = username.lstrip("@")
        if full_name:
            return f'<a href="https://t.me/{clean_username}">{full_name}</a>'
        else:
            # Если нет имени, используем username без @
            return f'<a href="https://t.me/{clean_username}">@{clean_username}</a>'
    
    # Если нет username или ссылки отключены, но есть имя - возвращаем просто текст
    if full_name:
        return full_name
    
    # Если нет ни того, ни другого - пропускаем
    return None


async def _load_template(template_path: Optional[str] = None) -> str:
    """
    Загружает шаблон из файла.
    
    Args:
        template_path: Путь к шаблону относительно корня проекта (опционально)
                      Если не указан, используется дефолтный templates/summarize_default.txt
    
    Returns:
        Содержимое шаблона как строка
    """
    project_root = Path(__file__).parent.parent
    
    if template_path:
        # Загружаем кастомный шаблон
        full_path = project_root / template_path
        if full_path.exists():
            async with aiofiles.open(full_path, mode='r', encoding='utf-8') as f:
                content = await f.read()
            logger.debug("Formatter.template.loaded → path=%s", template_path)
            return content.strip()
        else:
            logger.warning("Formatter.template.not_found → path=%s, using default", template_path)
    
    # Fallback на дефолтный шаблон
    default_path = project_root / "templates" / "summarize_default.txt"
    if default_path.exists():
        async with aiofiles.open(default_path, mode='r', encoding='utf-8') as f:
            content = await f.read()
        logger.debug("Formatter.template.default → path=templates/summarize_default.txt")
        return content.strip()
    else:
        # Если дефолтный шаблон не найден, возвращаем встроенный
        logger.warning("Formatter.template.default_not_found → using built-in template")
        return """<b>{{ header }}</b>

{% for topic in topics %}
• {% if topic.link %}<a href="{{ topic.link }}">{{ topic.topic }}</a>{% else %}{{ topic.topic }}{% endif %}{% if topic.message_count > 1 %} ({{ topic.message_count }}){% endif %}. {{ topic.description }}{% if topic.participants %}
<i>{{ topic.participants | join(', ') }}</i>{% endif %}

{% endfor %}
{{ footer }}
"""


async def format_summary_with_links(
    topics: List[Dict], 
    chat_id: int, 
    messages: Optional[List[Dict]] = None,
    show_users: bool = False,
    user_list_length: int = 10,
    chat_username: Optional[str] = None,
    show_user_links: bool = True,
    use_daily_template: bool = False
) -> str:
    """
    Форматирует суммаризацию с ссылками на сообщения используя Jinja2 шаблоны.
    
    Args:
        topics: Список тем с сообщениями [{"topic": "...", "message_ids": [1,2,3], "participants": [...]}]
        chat_id: ID чата для формирования ссылок
        messages: Исходные сообщения для получения информации о пользователях (опционально)
        show_users: Показывать ли список участников в каждой теме
        user_list_length: Максимальное количество участников для отображения (0 = без ограничения)
        chat_username: Username чата для получения настроек из config.yaml (опционально)
        show_user_links: Показывать ли ссылки на профили участников
        use_daily_template: Если True, использует daily_summarize_template вместо summarize_template
    
    Returns:
        Отформатированная суммаризация
    """
    logger.debug(
        "Formatter.start → topics_count=%d chat_id=%s show_users=%s user_list_length=%d show_user_links=%s use_daily_template=%s",
        len(topics) if topics else 0,
        str(chat_id),
        show_users,
        user_list_length,
        show_user_links,
        use_daily_template
    )
    
    # Получаем путь к шаблону из config.yaml
    template_path = None
    try:
        chat_settings = get_chat_settings(chat_id, chat_username)
        summarize_cfg = chat_settings.get("summarize") or {}
        
        if use_daily_template:
            # Для daily summarize сначала пробуем daily_summarize_template
            template_path = summarize_cfg.get("daily_summarize_template")
            if template_path:
                logger.debug("Formatter.template.daily_config → path=%s", template_path)
            else:
                # Fallback на обычный summarize_template если daily_summarize_template не задан
                template_path = summarize_cfg.get("summarize_template")
                if template_path:
                    logger.debug("Formatter.template.daily_fallback → path=%s", template_path)
        else:
            # Для обычной суммаризации используем summarize_template
            template_path = summarize_cfg.get("summarize_template")
            if template_path:
                logger.debug("Formatter.template.config → path=%s", template_path)
    except Exception as e:
        logger.exception("Failed to read template from config.yaml: %s", e)
    
    # Загружаем шаблон
    template_content = await _load_template(template_path)
    template = Template(template_content)
    
    # Строим словарь пользователей если нужно показывать участников
    users_dict = {}
    username_to_user_id = {}
    if show_users and messages:
        users_dict, username_to_user_id = _build_users_dict(messages)
        logger.debug("Formatter.users_dict → users_count=%d username_map_count=%d", len(users_dict), len(username_to_user_id))
    
    # Значения для header и footer
    header_text = "Сегодня обсуждали:"
    footer_text = "#summarize"
    telegram_limit = 4096
    
    # Рендерим шаблон с пустым списком топиков для получения базовой длины (header + footer)
    base_result = template.render(
        header=header_text,
        topics=[],
        footer=footer_text
    )
    base_length = len(base_result)
    
    # Проверяем базовую длину
    if base_length >= telegram_limit:
        logger.warning("Formatter.length_limit → base_length=%d >= %d, returning header+footer only", base_length, telegram_limit)
        return base_result.strip()
    
    # Подготавливаем данные для шаблона и постепенно добавляем топики
    formatted_topics = []
    topics_to_include = []
    
    for topic_data in topics:
        topic = topic_data.get("topic", "")
        message_ids = topic_data.get("message_ids", [])
        topic_description = topic_data.get("topic_description", "")
        message_count = topic_data.get("message_count", 1)
        participants = topic_data.get("participants", [])

        if not topic:
            continue
        
        # Одна ссылка: на первое (самое раннее) сообщение темы
        first_link = ""
        if message_ids:
            try:
                first_id = min(message_ids)
                first_link = format_message_link(chat_id, first_id)
            except Exception:
                first_link = ""
        
        # Формируем список участников если включено
        formatted_participants = []
        if show_users and participants and users_dict:
            # Ограничиваем количество участников
            participants_to_show = participants[:user_list_length] if user_list_length > 0 else participants
            
            for participant in participants_to_show:
                # Новая структура UserItem: username, first_name, second_name, message_count
                username = participant.get("username")
                first_name = participant.get("first_name")
                second_name = participant.get("second_name")
                
                # Пытаемся найти пользователя по username в словаре
                user_info = None
                found_user_id = None
                
                if username:
                    clean_username = username.lstrip("@").lower()
                    mapped_user_id = username_to_user_id.get(clean_username)
                    if mapped_user_id:
                        user_info = users_dict.get(mapped_user_id)
                        found_user_id = mapped_user_id
                
                # Если нашли пользователя в словаре, используем его данные
                if user_info:
                    formatted_name = _format_user_name(user_info, found_user_id if found_user_id is not None else 0, show_user_links)
                    if formatted_name:
                        formatted_participants.append(formatted_name)
                else:
                    # Если не нашли в словаре, формируем имя из данных UserItem
                    name_parts = []
                    if first_name:
                        name_parts.append(first_name)
                    if second_name:
                        name_parts.append(second_name)
                    full_name = " ".join(name_parts).strip()
                    
                    if username and show_user_links:
                        clean_username = username.lstrip("@")
                        if full_name:
                            formatted_name = f'<a href="https://t.me/{clean_username}">{full_name}</a>'
                        else:
                            formatted_name = f'<a href="https://t.me/{clean_username}">@{clean_username}</a>'
                    elif full_name:
                        formatted_name = full_name
                    else:
                        continue  # Пропускаем если нет данных
                    
                    if formatted_name:
                        formatted_participants.append(formatted_name)
        
        # Добавляем топик в список
        topic_dict = {
            "topic": topic,
            "link": first_link,
            "description": topic_description,
            "message_count": message_count,
            "participants": formatted_participants
        }
        formatted_topics.append(topic_dict)
        
        # Рендерим шаблон с текущим количеством топиков для проверки длины
        test_result = template.render(
            header=header_text,
            topics=formatted_topics,
            footer=footer_text
        )
        test_length = len(test_result)
        
        # Проверяем, не превысили ли лимит
        if test_length > telegram_limit:
            logger.debug(
                "Formatter.length_limit → stopping at topic %d, test_len=%d limit=%d",
                len(formatted_topics) - 1,
                test_length,
                telegram_limit
            )
            # Убираем последний топик, который не поместился
            formatted_topics.pop()
            break
        
        # Топик поместился, сохраняем результат
        topics_to_include = formatted_topics.copy()
    
    # Рендерим финальный результат с включенными топиками
    result = template.render(
        header=header_text,
        topics=topics_to_include,
        footer=footer_text
    )
    
    final_text = result.strip()
    logger.debug("Formatter.done ← summary_len=%d topics_count=%d", len(final_text), len(topics_to_include))
    return final_text
