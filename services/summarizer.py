"""Сервис для суммаризации сообщений."""
from typing import List, Dict, Optional
from services.openai_service import OpenAIService
from utils.prompt_loader import load_prompt, load_prompt_by_path
from utils.chat_config import get_chat_settings
import logging


logger = logging.getLogger(__name__)


class SummarizerService:
    """Сервис для суммаризации сообщений чата."""
    
    def __init__(self):
        """Инициализация сервиса."""
        self.openai_service = OpenAIService()
        self.relevance_prompt = None
        self.summarization_prompt = None
    
    async def initialize_prompts(self):
        """Загружает промпты из файлов."""
        self.relevance_prompt = await load_prompt("relevance_prompt.txt")
        self.summarization_prompt = await load_prompt("summarization_prompt.txt")
    
    async def _load_custom_prompt(self, chat_id: int, chat_username: Optional[str] = None) -> Optional[str]:
        """
        Загружает кастомный промпт из конфига для конкретного чата.
        
        Args:
            chat_id: ID чата
            chat_username: Username чата (опционально)
        
        Returns:
            Содержимое кастомного промпта или None, если не указан
        """
        try:
            chat_settings = get_chat_settings(chat_id, chat_username)
            topics_cfg = chat_settings.get("topics") or {}
            custom_prompt_path = topics_cfg.get("prompt")
            
            if custom_prompt_path:
                logger.info(
                    "Summarizer.load_custom_prompt → chat_id=%s username=%s path=%s",
                    chat_id,
                    chat_username or "-",
                    custom_prompt_path,
                )
                return await load_prompt_by_path(custom_prompt_path)
        except Exception as e:
            logger.warning(
                "Summarizer.load_custom_prompt.error → chat_id=%s username=%s error=%s",
                chat_id,
                chat_username or "-",
                e,
            )
        
        return None
    
    async def _load_custom_structured_system_prompt(self, chat_id: int, chat_username: Optional[str] = None) -> Optional[str]:
        """
        Загружает кастомный structured_system_prompt из конфига для конкретного чата.
        
        Args:
            chat_id: ID чата
            chat_username: Username чата (опционально)
        
        Returns:
            Содержимое кастомного structured_system_prompt или None, если не указан
        """
        try:
            chat_settings = get_chat_settings(chat_id, chat_username)
            topics_cfg = chat_settings.get("topics") or {}
            custom_prompt_path = topics_cfg.get("structured_system_prompt")
            
            if custom_prompt_path:
                logger.info(
                    "Summarizer.load_custom_structured_system_prompt → chat_id=%s username=%s path=%s",
                    chat_id,
                    chat_username or "-",
                    custom_prompt_path,
                )
                return await load_prompt_by_path(custom_prompt_path)
        except Exception as e:
            logger.warning(
                "Summarizer.load_custom_structured_system_prompt.error → chat_id=%s username=%s error=%s",
                chat_id,
                chat_username or "-",
                e,
            )
        
        return None
    
    async def filter_relevant_messages(
        self, 
        messages: List[Dict]
    ) -> List[Dict]:
        """
        Фильтрует релевантные сообщения для суммаризации.
        
        Args:
            messages: Список сообщений [{"text": "...", "message_id": 123}]
        
        Returns:
            Отфильтрованный список релевантных сообщений
        """
        if not self.relevance_prompt:
            await self.initialize_prompts()
        
        relevant_messages = []
        
        for message in messages:
            text = message.get("text", "").strip()
            if not text:
                continue
            
            # Анализируем релевантность через OpenAI
            is_relevant = await self.openai_service.analyze_relevance(
                text, 
                self.relevance_prompt
            )
            
            if is_relevant:
                relevant_messages.append(message)
        
        return relevant_messages
    
    async def summarize(
        self, 
        messages: List[Dict],
        chat_id: Optional[int] = None,
        chat_username: Optional[str] = None
    ) -> List[Dict]:
        """
        Суммаризирует сообщения по темам.
        
        Args:
            messages: Список сообщений [{"text": "...", "message_id": 123}]
            chat_id: ID чата для загрузки кастомного промпта (опционально)
            chat_username: Username чата для загрузки кастомного промпта (опционально)
        
        Returns:
            Список тем [{"topic": "...", "message_ids": [1,2,3]}]
        """
        logger.info("Summarizer.start → incoming_messages=%d", len(messages) if messages else 0)
        
        # Загружаем промпт: сначала пытаемся загрузить кастомный из конфига, затем стандартный
        prompt_to_use = None
        is_custom_prompt = False
        if chat_id is not None:
            prompt_to_use = await self._load_custom_prompt(chat_id, chat_username)
            if prompt_to_use is not None:
                is_custom_prompt = True
        
        if prompt_to_use is None:
            if not self.summarization_prompt:
                await self.initialize_prompts()
            prompt_to_use = self.summarization_prompt
        
        # Загружаем кастомный structured_system_prompt, если указан
        custom_structured_system_prompt = None
        is_custom_structured_prompt = False
        if chat_id is not None:
            custom_structured_system_prompt = await self._load_custom_structured_system_prompt(chat_id, chat_username)
            if custom_structured_system_prompt is not None:
                is_custom_structured_prompt = True
        
        logger.info(
            "Summarizer.prompt → chat_id=%s username=%s custom=%s prompt_len=%d structured_custom=%s",
            chat_id or "-",
            chat_username or "-",
            is_custom_prompt,
            len(prompt_to_use) if prompt_to_use else 0,
            is_custom_structured_prompt,
        )
        
        # Фильтруем релевантные сообщения
        #relevant_messages = await self.filter_relevant_messages(messages)
        # Временно октлючено 
        relevant_messages = messages
        logger.info(
            "Summarizer.filter → relevant_messages=%d (filtering_disabled=%s)",
            len(relevant_messages) if relevant_messages else 0,
            True
        )
        
        if not relevant_messages:
            logger.warning("Summarizer.exit → no relevant messages, nothing to summarize")
            return []
        
        # Получаем max_output_tokens из конфига
        max_output_tokens = None
        try:
            chat_settings = get_chat_settings(chat_id, chat_username)
            summarize_cfg = chat_settings.get("summarize") or {}
            max_output_tokens_value = summarize_cfg.get("max_output_tokens")
            if max_output_tokens_value is not None:
                max_output_tokens = int(max_output_tokens_value) if max_output_tokens_value > 0 else None
                logger.info(
                    "Summarizer.max_output_tokens → chat_id=%s username=%s max_output_tokens=%s",
                    chat_id or "-",
                    chat_username or "-",
                    max_output_tokens if max_output_tokens else "unlimited",
                )
        except Exception as e:
            logger.warning(
                "Summarizer.max_output_tokens.error → chat_id=%s username=%s error=%s",
                chat_id or "-",
                chat_username or "-",
                e,
            )
        
        # Суммаризируем через OpenAI
        topics = await self.openai_service.summarize_messages(
            relevant_messages,
            prompt_to_use,
            structured_system_prompt=custom_structured_system_prompt,
            max_output_tokens=max_output_tokens
        )
        topics_count = len(topics) if topics else 0
        size_preview = [
            {
                "message_count": t.get("message_count", 1),
                "topic_len": len((t.get("topic") or "")),
            }
            for t in (topics or [])[:5]
        ]
        logger.info("Summarizer.done ← topics_count=%d", topics_count)
        logger.debug("Summarizer.preview ← first_topics=%s", size_preview)
        
        return topics

