"""Сервис для обработки ссылок и их суммаризации."""

import logging
import re
from html import unescape
from typing import List, Tuple, Optional
from urllib.parse import urlparse, parse_qs
import xml.etree.ElementTree as ET

import httpx
from markdownify import markdownify as md_to_markdown

from services.openai_service import OpenAIService
from utils.prompt_loader import load_prompt
from utils.chat_config import get_chat_settings
from youtube_transcript_api import YouTubeTranscriptApi, TranscriptsDisabled, NoTranscriptFound


logger = logging.getLogger(__name__)


class LinkSummarizerService:
    """
    Сервис, который:
    - находит ссылки в тексте,
    - пытается скачать содержимое по ссылке (включая YouTube по субтитрам),
    - делает краткий пересказ для сохранения и (опционально) для вывода в чат.
    """

    def __init__(self) -> None:
        self.openai_service = OpenAIService()
        # Ограничение длины передаваемого в LLM текста
        self.max_content_chars: int = 16000
        # Ограничение количества обрабатываемых ссылок в одном сообщении
        self.max_links_per_message: int = 3
        # Кэш для промптов (загружаются лениво)
        self._storage_prompt_template: Optional[str] = None
        self._display_prompt_template: Optional[str] = None

    async def process_text(
        self,
        text: str,
        *,
        links_summarize: bool,
        links_summarize_show: bool,
        youtube_summarize: bool,
        youtube_summarize_show: bool,
        chat_id: Optional[int] = None,
        chat_username: Optional[str] = None,
    ) -> Tuple[str, List[str]]:
        """
        Находит ссылки в тексте и, при включённых флагах, возвращает:
        - suffix_for_storage: строку, которую нужно ДОПИСАТЬ в конец исходного текста перед сохранением в БД;
        - display_blocks: список готовых текстов для отправки в чат.

        Для обычных ссылок используются флаги links_summarize / links_summarize_show.
        Для YouTube-ссылок — отдельные флаги youtube_summarize / youtube_summarize_show.
        """
        if not text or not (
            links_summarize
            or links_summarize_show
            or youtube_summarize
            or youtube_summarize_show
        ):
            return "", []

        urls = self._extract_urls(text)
        if not urls:
            return "", []

        logger.info(
            "LinkSummarizer.detected_links → count=%d urls=%s "
            "links_summarize=%s links_summarize_show=%s "
            "youtube_summarize=%s youtube_summarize_show=%s",
            len(urls),
            urls,
            links_summarize,
            links_summarize_show,
            youtube_summarize,
            youtube_summarize_show,
        )

        # Ограничиваем количество ссылок на сообщение
        urls = urls[: self.max_links_per_message]

        storage_lines: List[str] = []
        display_blocks: List[str] = []

        for url in urls:
            try:
                logger.info("LinkSummarizer.process_text → url=%s", url)

                parsed = urlparse(url)
                host = (parsed.netloc or "").lower()
                is_youtube = any(h in host for h in ["youtube.com", "youtu.be"])

                summarize_flag = youtube_summarize if is_youtube else links_summarize
                show_flag = youtube_summarize_show if is_youtube else links_summarize_show

                if not summarize_flag and not show_flag:
                    logger.info(
                        "LinkSummarizer.process_text.skip_flags → url=%s is_youtube=%s",
                        url,
                        is_youtube,
                    )
                    continue
                content = await self._fetch_content_for_url(url)
                if not content:
                    logger.info("LinkSummarizer.process_text.skip → url=%s reason=no_content", url)
                    continue

                truncated = content[: self.max_content_chars]

                if summarize_flag:
                    brief_summary = await self._summarize_for_storage(url, truncated, chat_id=chat_id, chat_username=chat_username)
                    if brief_summary:
                        storage_lines.append(brief_summary)

                if show_flag:
                    display_summary = await self._summarize_for_display(url, truncated, chat_id=chat_id, chat_username=chat_username)
                    if display_summary:
                        display_blocks.append(display_summary)
            except Exception as e:
                logger.exception("LinkSummarizer.process_text.error → url=%s error=%s", url, e)
                continue

        suffix_for_storage = ""
        if storage_lines:
            suffix_for_storage = "\n\n" + "\n".join(storage_lines)

        return suffix_for_storage, display_blocks

    def _extract_urls(self, text: str) -> List[str]:
        """
        Простое извлечение URL из текста.
        Используем регулярку как универсальное решение (в дополнение к entities в хендлере).
        """
        # Базовая регулярка для ссылок http/https
        url_pattern = re.compile(
            r"(https?://[^\s]+)",
            flags=re.IGNORECASE,
        )
        urls = url_pattern.findall(text or "")
        # Убираем хвостовые знаки препинания
        cleaned: List[str] = []
        for u in urls:
            cleaned.append(u.rstrip(").,!?\"'»"))
        # Убрать дубликаты, сохранив порядок
        seen = set()
        result: List[str] = []
        for u in cleaned:
            if u not in seen:
                seen.add(u)
                result.append(u)
        return result

    async def _fetch_content_for_url(self, url: str) -> Optional[str]:
        """
        Получает текстовое содержимое по ссылке.
        - Для YouTube пытается скачать субтитры.
        - Для обычных сайтов забирает HTML/текст и передаёт его в LLM как есть.
        """
        if not url:
            return None

        parsed = urlparse(url)
        host = (parsed.netloc or "").lower()

        # Специальная обработка YouTube
        if any(h in host for h in ["youtube.com", "youtu.be"]):
            video_id = self._extract_youtube_video_id(parsed)
            if video_id:
                transcript = await self._fetch_youtube_transcript(video_id)
                if transcript:
                    logger.info(
                        "LinkSummarizer.youtube_transcript.ok → video_id=%s len=%d",
                        video_id,
                        len(transcript),
                    )
                    return transcript
                else:
                    logger.info(
                        "LinkSummarizer.youtube_transcript.empty → video_id=%s",
                        video_id,
                    )

        # Обычный HTTP-запрос
        try:
            async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
                response = await client.get(
                    url,
                    headers={
                        "User-Agent": (
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/122.0 Safari/537.36"
                        )
                    },
                )
                if response.status_code >= 400:
                    logger.warning(
                        "LinkSummarizer.http.error → url=%s status=%s",
                        url,
                        response.status_code,
                    )
                    return None

                content_type = response.headers.get("Content-Type", "").lower()
                if "text" not in content_type and "json" not in content_type and "xml" not in content_type and "html" not in content_type:
                    logger.info(
                        "LinkSummarizer.http.skip_binary → url=%s content_type=%s",
                        url,
                        content_type,
                    )
                    return None

                raw_text = response.text or ""

                # Если это HTML — конвертируем в Markdown через markdownify,
                # чтобы LLM работала с чистым текстом и списками, а не с тегами.
                is_html = "html" in content_type or "<html" in raw_text.lower() or "<body" in raw_text.lower()
                if is_html and raw_text.strip():
                    try:
                        markdown = md_to_markdown(
                            raw_text,
                            heading_style="ATX",
                            strip=["script", "style", "noscript"],
                        )
                        markdown = markdown.strip()
                        if markdown:
                            logger.info(
                                "LinkSummarizer.http.html_to_markdown.ok → url=%s orig_len=%d md_len=%d",
                                url,
                                len(raw_text),
                                len(markdown),
                            )
                            return markdown
                    except Exception as conv_err:
                        logger.exception(
                            "LinkSummarizer.http.html_to_markdown.error → url=%s error=%s",
                            url,
                            conv_err,
                        )
                        # Фолбэк: отдаем сырой HTML-текст
                        return raw_text

                return raw_text
        except Exception as e:
            logger.exception("LinkSummarizer.http.exception → url=%s error=%s", url, e)
            return None

    def _extract_youtube_video_id(self, parsed_url) -> Optional[str]:
        """
        Извлекает идентификатор видео YouTube из URL.
        Поддерживаются форматы:
        - https://www.youtube.com/watch?v=VIDEO_ID
        - https://youtu.be/VIDEO_ID
        - https://www.youtube.com/shorts/VIDEO_ID
        """
        try:
            host = (parsed_url.netloc or "").lower()
            path = parsed_url.path or ""
            query = parse_qs(parsed_url.query or "")

            if "youtu.be" in host:
                # /VIDEO_ID
                video_id = path.lstrip("/").split("/")[0]
                return video_id or None

            if "youtube.com" in host:
                if path.startswith("/watch"):
                    video_ids = query.get("v")
                    if video_ids:
                        return video_ids[0]
                if path.startswith("/shorts/"):
                    parts = path.split("/")
                    if len(parts) >= 3 and parts[2]:
                        return parts[2]
            return None
        except Exception:
            return None

    async def _fetch_youtube_transcript(self, video_id: str) -> Optional[str]:
        """
        Получает субтитры YouTube с помощью youtube-transcript-api.
        Предпочитаем русские субтитры, затем английские, затем любой доступный язык.
        Возвращаем объединённый текст субтитров одной строкой.

        Используется библиотека youtube-transcript-api:
        https://pypi.org/project/youtube-transcript-api/
        """
        if not video_id:
            return None

        try:
            # Получаем список доступных транскриптов
            transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
        except TranscriptsDisabled:
            logger.info(
                "LinkSummarizer.youtube_transcript.disabled → video_id=%s",
                video_id,
            )
            return None
        except NoTranscriptFound:
            logger.info(
                "LinkSummarizer.youtube_transcript.not_found → video_id=%s",
                video_id,
            )
            return None
        except Exception as e:
            logger.exception(
                "LinkSummarizer.youtube_transcript.list_error → video_id=%s error=%s",
                video_id,
                e,
            )
            return None

        # Пытаемся найти сначала вручную созданные, затем сгенерированные субтитры
        # в приоритетных языках (ru, en)
        preferred_languages = ["ru", "en"]

        def _fetch_from_transcript(transcript) -> Optional[str]:
            try:
                segments = transcript.fetch()
            except Exception as e:
                logger.exception(
                    "LinkSummarizer.youtube_transcript.fetch_error → video_id=%s lang=%s error=%s",
                    video_id,
                    getattr(transcript, "language_code", "unknown"),
                    e,
                )
                return None

            pieces: List[str] = []
            for seg in segments:
                text = (seg or {}).get("text") or ""
                text = " ".join(str(text).split())
                if text:
                    pieces.append(text)
            if not pieces:
                return None
            return " ".join(pieces)

        # 1) Предпочитаем ручные/авторские субтитры на ru/en
        for lang in preferred_languages:
            try:
                transcript = transcript_list.find_manually_created_transcript([lang])
                text = _fetch_from_transcript(transcript)
                if text:
                    logger.info(
                        "LinkSummarizer.youtube_transcript.ok → video_id=%s lang=%s type=manual len=%d",
                        video_id,
                        lang,
                        len(text),
                    )
                    return text
            except Exception:
                continue

        # 2) Затем авто-сгенерированные субтитры на ru/en
        for lang in preferred_languages:
            try:
                transcript = transcript_list.find_generated_transcript([lang])
                text = _fetch_from_transcript(transcript)
                if text:
                    logger.info(
                        "LinkSummarizer.youtube_transcript.ok → video_id=%s lang=%s type=generated len=%d",
                        video_id,
                        lang,
                        len(text),
                    )
                    return text
            except Exception:
                continue

        # 3) Фолбэк: любой доступный транскрипт
        try:
            for transcript in transcript_list:
                text = _fetch_from_transcript(transcript)
                if text:
                    logger.info(
                        "LinkSummarizer.youtube_transcript.ok → video_id=%s lang=%s type=any len=%d",
                        video_id,
                        getattr(transcript, "language_code", "unknown"),
                        len(text),
                    )
                    return text
        except Exception as e:
            logger.exception(
                "LinkSummarizer.youtube_transcript.iter_error → video_id=%s error=%s",
                video_id,
                e,
            )

        logger.info(
            "LinkSummarizer.youtube_transcript.empty_all → video_id=%s",
            video_id,
        )
        return None

    def _parse_youtube_xml_subtitles(self, xml_text: str) -> Optional[str]:
        """
        Простой парсинг XML субтитров YouTube: вытаскиваем все теги <text>.
        """
        try:
            root = ET.fromstring(xml_text)
        except Exception:
            return None

        pieces: List[str] = []
        for node in root.findall(".//text"):
            raw = node.text or ""
            raw = unescape(raw)
            cleaned = " ".join(raw.split())
            if cleaned:
                pieces.append(cleaned)

        if not pieces:
            return None

        return " ".join(pieces)

    async def _load_storage_prompt_if_needed(self) -> None:
        """Загружает промпт для суммаризации ссылок для хранения, если он еще не загружен."""
        if self._storage_prompt_template is None:
            self._storage_prompt_template = await load_prompt("link_summarize_storage_prompt.txt")

    async def _load_display_prompt_if_needed(self) -> None:
        """Загружает промпт для суммаризации ссылок для отображения, если он еще не загружен."""
        if self._display_prompt_template is None:
            self._display_prompt_template = await load_prompt("link_summarize_display_prompt.txt")

    async def _summarize_for_storage(self, url: str, content: str, chat_id: Optional[int] = None, chat_username: Optional[str] = None) -> Optional[str]:
        """
        Короткий пересказ для сохранения в БД (одна фраза в стиле:
        «По ссылке расположено/расположен/расположена ...»).
        """
        if not content:
            return None

        # Загружаем промпт из файла (если еще не загружен)
        await self._load_storage_prompt_if_needed()

        system_prompt = self._storage_prompt_template
        user_content = (
            f"URL: {url}\n\n"
            "Содержимое страницы или субтитров (может быть HTML или обычный текст):\n"
            f"{content}"
        )

        # Получаем max_output_tokens из конфига
        max_tokens = None
        try:
            if chat_id is not None:
                chat_settings = get_chat_settings(chat_id, chat_username)
                summarize_cfg = chat_settings.get("summarize") or {}
                max_output_tokens_value = summarize_cfg.get("max_output_tokens")
                if max_output_tokens_value is not None:
                    max_tokens = int(max_output_tokens_value) if max_output_tokens_value > 0 else None
        except Exception as e:
            logger.debug("LinkSummarizer.max_output_tokens.error → chat_id=%s error=%s", chat_id or "-", e)
        
        # Используем значение из конфига, если указано, иначе дефолтное значение
        max_tokens_to_use = max_tokens if max_tokens is not None and max_tokens > 0 else 32768

        try:
            response = await self.openai_service._chat_completion_with_retries(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                temperature=0.3,
                max_tokens=max_tokens_to_use,
            )
            summary = (response.choices[0].message.content or "").strip()
            if not summary:
                return None
            # На всякий случай убираем лишние переводы строк
            summary = " ".join(summary.split())
            return summary
        except Exception as e:
            logger.exception("LinkSummarizer.summarize_for_storage.error → url=%s error=%s", url, e)
            return None

    async def _summarize_for_display(self, url: str, content: str, chat_id: Optional[int] = None, chat_username: Optional[str] = None) -> Optional[str]:
        """
        Тезисный пересказ для вывода в чат.
        Формат:
        Первая строка: Краткий заголовок (URL): одно предложение.
        Далее несколько строк, начинающихся с «– », с ключевыми фактами.
        """
        if not content:
            return None

        # Загружаем промпт из файла (если еще не загружен)
        await self._load_display_prompt_if_needed()

        # Заменяем плейсхолдер {url} в промпте на реальный URL
        system_prompt = self._display_prompt_template.replace("{url}", url) if self._display_prompt_template else None
        if not system_prompt:
            logger.error("LinkSummarizer._summarize_for_display → failed to load prompt template")
            return None

        user_content = (
            f"URL: {url}\n\n"
            "Содержимое страницы или субтитров (может быть HTML или обычный текст):\n"
            f"{content}"
        )

        # Получаем max_output_tokens из конфига
        max_tokens = None
        try:
            if chat_id is not None:
                chat_settings = get_chat_settings(chat_id, chat_username)
                summarize_cfg = chat_settings.get("summarize") or {}
                max_output_tokens_value = summarize_cfg.get("max_output_tokens")
                if max_output_tokens_value is not None:
                    max_tokens = int(max_output_tokens_value) if max_output_tokens_value > 0 else None
        except Exception as e:
            logger.debug("LinkSummarizer.max_output_tokens.error → chat_id=%s error=%s", chat_id or "-", e)
        
        # Используем значение из конфига, если указано, иначе дефолтное значение
        max_tokens_to_use = max_tokens if max_tokens is not None and max_tokens > 0 else 32768

        try:
            response = await self.openai_service._chat_completion_with_retries(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                temperature=0.4,
                max_tokens=max_tokens_to_use,
            )
            summary = (response.choices[0].message.content or "").strip()
            if not summary:
                return None
            return summary
        except Exception as e:
            logger.exception("LinkSummarizer.summarize_for_display.error → url=%s error=%s", url, e)
            return None


