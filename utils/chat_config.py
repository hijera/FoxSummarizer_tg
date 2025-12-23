"""Загрузка и выбор настроек чата из config.yaml."""
from __future__ import annotations

from typing import Any, Dict, Optional
import os
import logging
import re
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

try:
    import yaml
except Exception as e:
    yaml = None  # будет ошибка при первом обращении, если не установлено

_CONFIG_CACHE: Dict[str, Any] | None = None
_CONFIG_PATH: str = "config.yaml"

logger = logging.getLogger(__name__)


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Простое глубокое слияние словарей base <- override."""
    result: Dict[str, Any] = dict(base) if base else {}
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(result.get(k), dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def _normalize_username(username: Optional[str]) -> Optional[str]:
    if not username:
        return None
    handle = username.strip()
    if handle.startswith("@"):
        handle = handle[1:]
    return handle.lower()


def _parse_timezone(timezone_str: str) -> Optional[timezone]:
    """
    Парсит строку временной зоны в двух форматах:
    1. IANA формат: "Europe/Moscow", "America/New_York" и т.д.
    2. UTC смещение: "+03:00", "-05:00", "+03:00:00", "-05:30" и т.д.
    
    Возвращает объект timezone или None при ошибке.
    """
    if not timezone_str:
        return None
    
    tz_str = str(timezone_str).strip()
    
    # Проверяем формат UTC смещения: +HH:MM или -HH:MM (опционально с секундами)
    utc_offset_pattern = re.compile(r'^([+-])(\d{1,2}):(\d{2})(?::(\d{2}))?$')
    match = utc_offset_pattern.match(tz_str)
    
    if match:
        # Формат UTC смещения
        sign = match.group(1)
        hours = int(match.group(2))
        minutes = int(match.group(3))
        seconds = int(match.group(4) or 0)
        
        if hours > 23 or minutes > 59 or seconds > 59:
            logger.warning(
                "Timezone.utc_offset_invalid → timezone=%s (hours/minutes/seconds out of range)",
                tz_str,
            )
            return None
        
        # Вычисляем общее смещение в секундах
        total_seconds = hours * 3600 + minutes * 60 + seconds
        if sign == '-':
            total_seconds = -total_seconds
        
        # Создаем timezone с фиксированным смещением
        return timezone(timedelta(seconds=total_seconds))
    else:
        # Пробуем IANA формат
        try:
            return ZoneInfo(tz_str)
        except Exception as exc:
            logger.warning(
                "Timezone.iana_invalid → timezone=%s error=%s",
                tz_str,
                exc,
            )
            return None


def load_yaml_config(path: str = "config.yaml") -> Dict[str, Any]:
    """
    Загружает YAML-конфиг. Возвращает {} если файла нет.
    Использует простое кэширование в памяти.
    """
    global _CONFIG_CACHE, _CONFIG_PATH

    if _CONFIG_CACHE is not None and path == _CONFIG_PATH:
        return _CONFIG_CACHE

    _CONFIG_PATH = path
    if not os.path.exists(path):
        _CONFIG_CACHE = {}
        return _CONFIG_CACHE

    if yaml is None:
        raise RuntimeError("Модуль PyYAML не установлен. Добавьте pyyaml в requirements.txt")

    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    _CONFIG_CACHE = data if isinstance(data, dict) else {}
    return _CONFIG_CACHE


def get_chat_settings(chat_id: int, chat_username: Optional[str] = None) -> Dict[str, Any]:
    """
    Возвращает объединённые настройки для чата:
    defaults + (по chat_id) + (по username), где username нормализуется (без '@', нижний регистр).
    Приоритет: chat_id > username > defaults.
    """
    data = load_yaml_config(_CONFIG_PATH)
    defaults = data.get("defaults") or {}
    chats = data.get("chats") or {}

    # YAML ключи — строки
    chat_id_key = str(chat_id)
    username_key = _normalize_username(chat_username) or ""

    result = dict(defaults)
    # приоритет chat_id
    if chat_id_key in chats:
        result = _deep_merge(result, chats.get(chat_id_key) or {})
    # затем username, если он есть и явно задан
    if username_key and username_key in chats:
        result = _deep_merge(result, chats.get(username_key) or {})

    return result


def get_day_window_for_chat(chat_id: int, chat_username: Optional[str] = None) -> Optional[tuple[int, int]]:
    """
    Возвращает (date_from, date_to) в UNIX-таймстампах для «прошедшего дня»
    на основе timezone и summarize.day_start_time из config.yaml.

    Логика:
    - Берётся timezone (строка в формате IANA "Europe/Moscow" или UTC смещение "+03:00") из корня настроек чата.
    - Берётся summarize.day_start_time (строка "ЧЧ:ММ").
    - Вычисляется последняя прошедшая отметка day_start_time (anchor) в этой таймзоне.
    - Окно «прошедшего дня» = [anchor - 24 часа, anchor) в UTC.

    Если настройки не заданы или некорректны, возвращает None.
    """
    settings = get_chat_settings(chat_id, chat_username)
    timezone_name = settings.get("timezone")
    summarize_cfg = settings.get("summarize") or {}
    day_start_str = summarize_cfg.get("day_start_time")

    if not timezone_name or not day_start_str:
        return None

    tz = _parse_timezone(str(timezone_name))
    if tz is None:
        logger.warning(
            "DayWindow.timezone_invalid → chat_id=%s username=%s timezone=%s",
            chat_id,
            chat_username or "-",
            timezone_name,
        )
        return None

    time_str = str(day_start_str).strip()
    try:
        hour_str, minute_str = time_str.split(":")
        hour = int(hour_str)
        minute = int(minute_str)
        if hour < 0 or hour > 23 or minute < 0 or minute > 59:
            raise ValueError("hour/minute out of range")
    except Exception as exc:
        logger.warning(
            "DayWindow.time_invalid → chat_id=%s username=%s day_start_time=%s error=%s",
            chat_id,
            chat_username or "-",
            day_start_str,
            exc,
        )
        return None

    now_utc = datetime.now(timezone.utc)
    now_local = now_utc.astimezone(tz)

    anchor_local = datetime(
        year=now_local.year,
        month=now_local.month,
        day=now_local.day,
        hour=hour,
        minute=minute,
        second=0,
        microsecond=0,
        tzinfo=tz,
    )

    if now_local < anchor_local:
        anchor_local = anchor_local - timedelta(days=1)

    start_local = anchor_local - timedelta(days=1)

    start_utc = start_local.astimezone(timezone.utc)
    anchor_utc = anchor_local.astimezone(timezone.utc)

    date_from = int(start_utc.timestamp())
    date_to = int(anchor_utc.timestamp())

    if date_from >= date_to:
        logger.warning(
            "DayWindow.invalid_range → chat_id=%s username=%s date_from=%s date_to=%s",
            chat_id,
            chat_username or "-",
            date_from,
            date_to,
        )
        return None

    return date_from, date_to


def get_daily_time_utc(chat_id: int, chat_username: Optional[str] = None) -> Optional[datetime]:
    """
    Возвращает datetime в UTC для времени запуска ежедневной суммаризации (daily_time)
    на основе timezone и summarize.daily_time из config.yaml.
    
    Логика:
    - Берётся timezone (строка в формате IANA "Europe/Moscow" или UTC смещение "+03:00") из корня настроек чата.
    - Берётся summarize.daily_time (строка "ЧЧ:ММ").
    - Вычисляется время запуска на сегодня в указанной временной зоне.
    - Преобразуется в UTC для сравнения с текущим UTC временем.

    Если настройки не заданы или некорректны, возвращает None.
    """
    settings = get_chat_settings(chat_id, chat_username)
    timezone_name = settings.get("timezone")
    summarize_cfg = settings.get("summarize") or {}
    daily_time_str = summarize_cfg.get("daily_time")
    
    if not timezone_name or not daily_time_str:
        return None
    
    tz = _parse_timezone(str(timezone_name))
    if tz is None:
        logger.warning(
            "DailyTime.timezone_invalid → chat_id=%s username=%s timezone=%s",
            chat_id,
            chat_username or "-",
            timezone_name,
        )
        return None
    
    time_str = str(daily_time_str).strip()
    try:
        hour_str, minute_str = time_str.split(":")
        hour = int(hour_str)
        minute = int(minute_str)
        if hour < 0 or hour > 23 or minute < 0 or minute > 59:
            raise ValueError("hour/minute out of range")
    except Exception as exc:
        logger.warning(
            "DailyTime.time_invalid → chat_id=%s username=%s daily_time=%s error=%s",
            chat_id,
            chat_username or "-",
            daily_time_str,
            exc,
        )
        return None
    
    # Получаем текущее время в UTC и конвертируем в локальную временную зону
    now_utc = datetime.now(timezone.utc)
    now_local = now_utc.astimezone(tz)
    
    # Создаем datetime для времени запуска сегодня в локальной временной зоне
    daily_time_local = datetime(
        year=now_local.year,
        month=now_local.month,
        day=now_local.day,
        hour=hour,
        minute=minute,
        second=0,
        microsecond=0,
        tzinfo=tz,
    )
    
    # Преобразуем в UTC (возвращаем время запуска на сегодня для сравнения)
    daily_time_utc = daily_time_local.astimezone(timezone.utc)
    
    return daily_time_utc


def get_summary_window_for_chat(chat_id: int, chat_username: Optional[str] = None) -> Optional[tuple[int, int]]:
    """
    Возвращает (date_from, date_to) в UNIX-таймстампах для окна суммаризации команды /summarize
    на основе timezone и summarize.summary_start_time из config.yaml.

    Логика:
    - Берётся timezone (строка в формате IANA "Europe/Moscow" или UTC смещение "+03:00") из корня настроек чата.
    - Берётся summarize.summary_start_time (строка "ЧЧ:ММ").
    - Вычисляется время начала сегодняшнего дня с указанным временем в этой таймзоне.
    - Окно суммаризации = [время начала сегодня, текущий момент) в UTC.

    Если summary_start_time не задан или некорректен, возвращает None (тогда берутся все сообщения).

    Returns:
        Кортеж (date_from, date_to) в UNIX-таймстампах или None, если параметр не задан
    """
    settings = get_chat_settings(chat_id, chat_username)
    timezone_name = settings.get("timezone")
    summarize_cfg = settings.get("summarize") or {}
    summary_start_time_str = summarize_cfg.get("summary_start_time")

    # Если параметр не задан, возвращаем None (брать все сообщения)
    if not summary_start_time_str:
        return None

    if not timezone_name:
        logger.warning(
            "SummaryWindow.timezone_missing → chat_id=%s username=%s",
            chat_id,
            chat_username or "-",
        )
        return None

    tz = _parse_timezone(str(timezone_name))
    if tz is None:
        logger.warning(
            "SummaryWindow.timezone_invalid → chat_id=%s username=%s timezone=%s",
            chat_id,
            chat_username or "-",
            timezone_name,
        )
        return None

    time_str = str(summary_start_time_str).strip()
    try:
        hour_str, minute_str = time_str.split(":")
        hour = int(hour_str)
        minute = int(minute_str)
        if hour < 0 or hour > 23 or minute < 0 or minute > 59:
            raise ValueError("hour/minute out of range")
    except Exception as exc:
        logger.warning(
            "SummaryWindow.time_invalid → chat_id=%s username=%s summary_start_time=%s error=%s",
            chat_id,
            chat_username or "-",
            summary_start_time_str,
            exc,
        )
        return None

    # Получаем текущее время в UTC и конвертируем в локальную временную зону
    now_utc = datetime.now(timezone.utc)
    now_local = now_utc.astimezone(tz)

    # Вычисляем время начала сегодняшнего дня с указанным временем
    start_local = datetime(
        year=now_local.year,
        month=now_local.month,
        day=now_local.day,
        hour=hour,
        minute=minute,
        second=0,
        microsecond=0,
        tzinfo=tz,
    )

    # Если время начала сегодняшнего дня в будущем, берем время начала предыдущего дня
    if now_local < start_local:
        start_local = start_local - timedelta(days=1)
        logger.debug(
            "SummaryWindow.start_time_future → chat_id=%s username=%s summary_start_time=%s (using previous day)",
            chat_id,
            chat_username or "-",
            summary_start_time_str,
        )

    # Преобразуем в UTC
    start_utc = start_local.astimezone(timezone.utc)
    date_from = int(start_utc.timestamp())
    date_to = int(now_utc.timestamp())

    # Проверяем, что окно валидно (на всякий случай)
    if date_from >= date_to:
        logger.warning(
            "SummaryWindow.invalid_range → chat_id=%s username=%s date_from=%s date_to=%s",
            chat_id,
            chat_username or "-",
            date_from,
            date_to,
        )
        return None

    return date_from, date_to


def is_voice_recognition_enabled(chat_id: int, chat_username: Optional[str] = None) -> bool:
    """
    Проверяет, включено ли распознавание голосовых сообщений для чата.
    
    Args:
        chat_id: ID чата
        chat_username: Username чата
    
    Returns:
        True если распознавание включено, False если отключено или не задано (по умолчанию False)
    """
    settings = get_chat_settings(chat_id, chat_username)
    voice_recognition_enabled = settings.get("voice_recognition_enabled")
    
    # Если параметр не задан, возвращаем False (по умолчанию отключено)
    if voice_recognition_enabled is None:
        return False
    
    # Преобразуем в булево значение
    return bool(voice_recognition_enabled)
