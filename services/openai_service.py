"""Сервис для работы с OpenAI API."""
import re
import asyncio
import random
import logging
import json
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional, Any
from openai import AsyncOpenAI
from pydantic import BaseModel, Field, conlist
try:
    # Новые исключения SDK 1.x
    from openai import RateLimitError, APIStatusError
except Exception:  # pragma: no cover
    RateLimitError = Exception  # fallback
    APIStatusError = Exception
from config import Config
from utils.prompt_loader import load_prompt

logger = logging.getLogger(__name__)

class UserItem(BaseModel):
    username: Optional[str] = Field(..., description="Username пользователя")
    first_name: Optional[str] = Field(..., description="Имя пользователя")
    second_name: Optional[str] = Field(..., description="Фамилия пользователя")
    message_count: int = Field(
        default=1,
        description="Количество сообщений этого участника в теме",
    )
    
class TopicItem(BaseModel):
    topic: str = Field(..., description="Заголовок темы (2-5 слов)")
    topic_description: str = Field(..., description="Краткое описание темы (1 предложение)")
    message_ids: conlist(int, min_length=1, max_length=1) = Field(
        default_factory=list,
        description="ТОЛЬКО id самого раннего сообщения темы (первое сообщение, с которого началась тема). Один message_id.",
    )
    message_count: int = Field(
        default=1,
        description="Количество сообщений в теме (сколько сообщений было сгруппировано в эту тему)",
    )
    participants: List[UserItem] = Field(
        default_factory=list,
        description="Список участников темы. Отсортировано по убыванию message_count (самый активный первый).",
    )


class TopicsResponse(BaseModel):
    topics: List[TopicItem] = Field(
        default_factory=list, description="Список тематических групп обсуждения"
    )


class OpenAIService:
    """Сервис для взаимодействия с OpenAI API."""
    
    def __init__(self):
        """Инициализация клиента OpenAI."""
        # Проверяем наличие API ключа перед инициализацией
        if not Config.OPENAI_API_KEY:
            raise ValueError("OPENAI_API_KEY не установлен в конфигурации")
        
        # Создаем клиент с явным указанием параметров
        client_kwargs = {
            "api_key": Config.OPENAI_API_KEY,
        }
        
        # Добавляем base_url только если он отличается от дефолтного
        if Config.OPENAI_BASE_URL and Config.OPENAI_BASE_URL != "https://api.openai.com/v1":
            client_kwargs["base_url"] = Config.OPENAI_BASE_URL
        
        self.client = AsyncOpenAI(**client_kwargs)
        self.model = Config.OPENAI_MODEL
        
        # Кэш для промптов (загружаются лениво)
        self._structured_system_prompt_template: Optional[str] = None
        self._structured_user_prompt_template: Optional[str] = None
        self._fallback_user_prompt_template: Optional[str] = None

        # Feature-detection для Structured Output (совместимость разных версий SDK)
        beta = getattr(self.client, "beta", None)
        self._supports_responses_parse = bool(
            hasattr(self.client, "responses") and hasattr(self.client.responses, "parse")
        )
        self._supports_beta_parse = bool(
            beta
            and hasattr(beta, "chat")
            and hasattr(beta.chat, "completions")
            and hasattr(beta.chat.completions, "parse")
        )
        # Throttling / Backoff
        self.min_delay_s = Config.OPENAI_MIN_DELAY_S
        self.max_retries = Config.OPENAI_MAX_RETRIES
        self.backoff_base_s = Config.OPENAI_BACKOFF_BASE_S
        self.backoff_max_s = Config.OPENAI_BACKOFF_MAX_S
        # Локальные файлы логов LLM
        self.logs_dir = Path("logs")
        self.chat_log_path = self.logs_dir / "llm_chat.log"
        self.structured_log_path = self.logs_dir / "llm_structured.log"
        try:
            self.logs_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.warning("LLM FileLogs → failed to create logs dir %s: %s", self.logs_dir, e)

    # ===== Локальное файловое логирование LLM-запросов/ответов =====
    def _write_log_line(self, path: Path, payload: Dict[str, Any]) -> None:
        """Записывает одну JSON-строку в указанный файл логов."""
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
                f.write("\n")
        except Exception as e:
            # Не мешаем основной работе сервиса, если логирование в файл не удалось
            logger.debug("LLM FileLogs → failed to write log line to %s: %s", path, e)

    def _log_chat_io(
        self,
        *,
        direction: str,
        model: str,
        temperature: float,
        max_tokens: int,
        messages: Optional[List[Dict[str, Any]]] = None,
        response_text: Optional[str] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Логирование обычных chat.completions в файл logs/llm_chat.log."""
        payload: Dict[str, Any] = {
            "ts": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "channel": "chat",
            "direction": direction,
            "model": model,
            "temperature": temperature,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if messages is not None:
            payload["messages"] = messages
        if response_text is not None:
            payload["response_text"] = response_text
        if extra:
            payload.update(extra)
        self._write_log_line(self.chat_log_path, payload)

    def _log_structured_io(
        self,
        *,
        direction: str,
        model: str,
        temperature: float,
        max_output_tokens: int,
        system: Optional[str] = None,
        input_text: Optional[str] = None,
        topics: Optional[List[Dict[str, Any]]] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Логирование Structured Output (responses/parse) в файл logs/llm_structured.log."""
        payload: Dict[str, Any] = {
            "ts": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "channel": "structured",
            "direction": direction,
            "model": model,
            "temperature": temperature,
        }
        if max_output_tokens is not None:
            payload["max_output_tokens"] = max_output_tokens
        if system is not None:
            payload["system"] = system
        if input_text is not None:
            payload["input"] = input_text
        if topics is not None:
            payload["topics"] = topics
        if extra:
            payload.update(extra)
        self._write_log_line(self.structured_log_path, payload)

    # ===== Общая пауза/бэкофф =====
    async def _sleep_with_jitter(self, base: float) -> None:
        """Пауза с небольшим джиттером для равномерного распределения нагрузки."""
        await asyncio.sleep(base + random.uniform(0, 0.3))

    async def _chat_completion_with_retries(
        self,
        messages: List[Dict[str, Any]],
        temperature: float,
        max_tokens: Optional[int],
    ):
        """Вызывает chat.completions.create с ретраями и бэкоффом на 429/временные ошибки."""
        # Базовая задержка перед каждым запросом, чтобы «запрашивало медленнее»
        await self._sleep_with_jitter(self.min_delay_s)

        # Логируем запрос к LLM
        system_msg = next((m.get("content", "") for m in messages if m.get("role") == "system"), "")
        user_msg = next((m.get("content", "") for m in messages if m.get("role") == "user"), "")
        max_tokens_str = str(max_tokens) if max_tokens is not None else "unlimited"
        logger.info(
            "LLM Request → model=%s temperature=%.2f max_tokens=%s system_len=%d user_len=%d",
            self.model, temperature, max_tokens_str, len(system_msg), len(user_msg)
        )
        logger.debug("LLM Request → system: %s", system_msg[:500] + ("..." if len(system_msg) > 500 else ""))
        logger.debug("LLM Request → user: %s", user_msg[:500] + ("..." if len(user_msg) > 500 else ""))
        # Файловое логирование полного запроса
        try:
            self._log_chat_io(
                direction="request",
                model=self.model,
                temperature=temperature,
                max_tokens=max_tokens,
                messages=messages,
                extra={
                    "system_preview_len": len(system_msg),
                    "user_preview_len": len(user_msg),
                },
            )
        except Exception:
            # Ошибка логирования не должна ломать основной поток
            pass

        attempt = 0
        last_exc: Optional[Exception] = None
        while attempt <= self.max_retries:
            try:
                # Если max_tokens равен 0 или None, не передаем параметр (без ограничения)
                create_kwargs = {
                    "model": self.model,
                    "messages": messages,
                    "temperature": temperature,
                }
                if max_tokens is not None and max_tokens > 0:
                    create_kwargs["max_tokens"] = max_tokens
                
                response = await self.client.chat.completions.create(**create_kwargs)
                # Логируем ответ от LLM
                response_text = response.choices[0].message.content if response.choices else ""
                logger.info(
                    "LLM Response ← model=%s tokens_used=%s finish_reason=%s response_len=%d",
                    self.model,
                    getattr(response.usage, "total_tokens", "N/A") if hasattr(response, "usage") else "N/A",
                    response.choices[0].finish_reason if response.choices else "N/A",
                    len(response_text)
                )
                logger.debug("LLM Response ← content: %s", response_text[:1000] + ("..." if len(response_text) > 1000 else ""))
                # Файловое логирование ответа
                try:
                    usage_total = getattr(response.usage, "total_tokens", None) if hasattr(response, "usage") else None
                    finish_reason = response.choices[0].finish_reason if response.choices else None
                    self._log_chat_io(
                        direction="response",
                        model=self.model,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        response_text=response_text,
                        extra={
                            "tokens_used": usage_total,
                            "finish_reason": finish_reason,
                        },
                    )
                except Exception:
                    pass
                return response
            except RateLimitError as e:  # 429
                last_exc = e
                logger.warning("LLM RateLimitError (429) → attempt=%d/%d", attempt + 1, self.max_retries + 1)
                # Уважаем Retry-After, если доступен
                retry_after = None
                try:
                    retry_after = int(getattr(e, "response", {}).headers.get("Retry-After", "0"))  # type: ignore[attr-defined]
                except Exception:
                    retry_after = None
                wait = retry_after if retry_after and retry_after > 0 else min(
                    self.backoff_base_s * (2 ** attempt), self.backoff_max_s
                )
                logger.debug("LLM Retry → waiting %.2fs", wait)
                await self._sleep_with_jitter(wait)
            except APIStatusError as e:
                last_exc = e
                # Если 429 — такой же бэкофф
                status = getattr(e, "status_code", None)
                if status == 429:
                    logger.warning("LLM APIStatusError (429) → attempt=%d/%d", attempt + 1, self.max_retries + 1)
                    wait = min(self.backoff_base_s * (2 ** attempt), self.backoff_max_s)
                    logger.debug("LLM Retry → waiting %.2fs", wait)
                    await self._sleep_with_jitter(wait)
                else:
                    logger.error("LLM APIStatusError → status=%s error=%s", status, str(e))
                    raise
            except Exception as e:
                last_exc = e
                logger.warning("LLM Exception → attempt=%d/%d error=%s", attempt + 1, self.max_retries + 1, str(e))
                # Считаем как временную ошибку и пробуем еще с бэкоффом
                wait = min(self.backoff_base_s * (2 ** attempt), self.backoff_max_s)
                logger.debug("LLM Retry → waiting %.2fs", wait)
                await self._sleep_with_jitter(wait)
            attempt += 1
        # Если все попытки исчерпаны — пробрасываем последнюю
        if last_exc:
            logger.error("LLM Request Failed → all retries exhausted: %s", str(last_exc))
            raise last_exc
        raise RuntimeError("Не удалось выполнить запрос к OpenAI без исключения, но и без результата")

    async def _responses_parse_with_retries(
        self,
        system: str,
        input_text: str,
        temperature: float,
        max_output_tokens: Optional[int],
    ):
        """
        Вызывает responses.parse с ретраями и бэкоффом для Structured Output.
        Возвращает экземпляр Pydantic модели _TopicsResponse.
        """
        # Базовая задержка перед каждым запросом
        await self._sleep_with_jitter(self.min_delay_s)

        # Логируем запрос к LLM (structured output)
        max_tokens_str = str(max_output_tokens) if max_output_tokens is not None else "unlimited"
        logger.info(
            "LLM Structured Request → model=%s temperature=%.2f max_output_tokens=%s system_len=%d input_len=%d",
            self.model, temperature, max_tokens_str, len(system), len(input_text)
        )
        logger.debug("LLM Structured Request → system: %s", system[:500] + ("..." if len(system) > 500 else ""))
        logger.debug("LLM Structured Request → input: %s", input_text[:500] + ("..." if len(input_text) > 500 else ""))
        # Файловое логирование structured-запроса
        try:
            self._log_structured_io(
                direction="request",
                model=self.model,
                temperature=temperature,
                max_output_tokens=max_output_tokens,
                system=system,
                input_text=input_text,
            )
        except Exception:
            pass

        attempt = 0
        last_exc: Optional[Exception] = None
        while attempt <= self.max_retries:
            try:
                # Используем доступный способ Structured Output
                # Если max_output_tokens равен 0 или None, не передаем параметр (без ограничения)
                parse_kwargs = {
                    "model": self.model,
                    "temperature": temperature,
                    "response_format": TopicsResponse,  # Pydantic-модель
                }
                if max_output_tokens is not None and max_output_tokens > 0:
                    parse_kwargs["max_output_tokens"] = max_output_tokens
                
                if self._supports_responses_parse:
                    parsed = await self.client.responses.parse(
                        system=system,
                        input=input_text,
                        **parse_kwargs
                    )
                elif self._supports_beta_parse:
                    # Для beta.parse используем max_tokens вместо max_output_tokens
                    beta_kwargs = {k: v for k, v in parse_kwargs.items() if k != "max_output_tokens"}
                    if max_output_tokens is not None and max_output_tokens > 0:
                        beta_kwargs["max_tokens"] = max_output_tokens
                    chat_resp = await self.client.beta.chat.completions.parse(
                        messages=[
                            {"role": "system", "content": system},
                            {"role": "user", "content": input_text},
                        ],
                        **beta_kwargs
                    )
                    # beta.parse возвращает parsed в message
                    parsed = chat_resp.choices[0].message.parsed  # type: ignore[attr-defined]
                else:
                    raise AttributeError("Structured Output недоступен: нет ни responses.parse, ни beta.chat.completions.parse")

                # Логируем ответ от LLM (у unified parsed ожидается TopicsResponse)
                topics_count = len(parsed.topics) if hasattr(parsed, "topics") and parsed.topics else 0
                logger.info("LLM Structured Response ← model=%s topics_count=%d", self.model, topics_count)
                if topics_count > 0:
                    topics_preview = [
                        {
                            "topic": t.topic[:100] + ("..." if len(t.topic) > 100 else ""),
                            "message_ids_count": len(t.message_ids),
                            "message_count": getattr(t, "message_count", 1),
                        }
                        for t in parsed.topics[:3]
                    ]
                    logger.debug("LLM Structured Response ← topics preview: %s", topics_preview)
                # Файловое логирование structured-ответа
                try:
                    topics_for_log: List[Dict[str, Any]] = []
                    if hasattr(parsed, "topics") and parsed.topics:
                        for t in parsed.topics:
                            try:
                                topics_for_log.append(
                                    {
                                        "topic": t.topic,
                                        "message_ids": list(t.message_ids or []),
                                        "message_count": getattr(t, "message_count", 1),
                                    }
                                )
                            except Exception:
                                continue
                    self._log_structured_io(
                        direction="response",
                        model=self.model,
                        temperature=temperature,
                        max_output_tokens=max_output_tokens,
                        system=system,
                        input_text=input_text,
                        topics=topics_for_log,
                        extra={"topics_count": topics_count},
                    )
                except Exception:
                    pass
                return parsed
            except RateLimitError as e:  # 429
                last_exc = e
                logger.warning("LLM Structured RateLimitError (429) → attempt=%d/%d", attempt + 1, self.max_retries + 1)
                retry_after = None
                try:
                    retry_after = int(getattr(e, "response", {}).headers.get("Retry-After", "0"))  # type: ignore[attr-defined]
                except Exception:
                    retry_after = None
                wait = retry_after if retry_after and retry_after > 0 else min(
                    self.backoff_base_s * (2 ** attempt), self.backoff_max_s
                )
                logger.debug("LLM Structured Retry → waiting %.2fs", wait)
                await self._sleep_with_jitter(wait)
            except APIStatusError as e:
                last_exc = e
                status = getattr(e, "status_code", None)
                if status == 429:
                    logger.warning("LLM Structured APIStatusError (429) → attempt=%d/%d", attempt + 1, self.max_retries + 1)
                    wait = min(self.backoff_base_s * (2 ** attempt), self.backoff_max_s)
                    logger.debug("LLM Structured Retry → waiting %.2fs", wait)
                    await self._sleep_with_jitter(wait)
                else:
                    logger.error("LLM Structured APIStatusError → status=%s error=%s", status, str(e))
                    raise
            except AttributeError as e:
                # Структурированный метод недоступен в установленной версии SDK
                last_exc = e
                logger.warning("LLM Structured AttributeError → structured parse недоступен: %s", str(e))
                break  # Дальше пробует фолбэк вне этого метода
            except TypeError as e:
                # Возможное несовпадение сигнатуры SDK — прерываем, чтобы пойти во фолбэк
                last_exc = e
                logger.warning("LLM Structured TypeError → несовпадение сигнатуры SDK: %s", str(e))
                break
            except Exception as e:
                last_exc = e
                logger.warning("LLM Structured Exception → attempt=%d/%d error=%s", attempt + 1, self.max_retries + 1, str(e))
                wait = min(self.backoff_base_s * (2 ** attempt), self.backoff_max_s)
                logger.debug("LLM Structured Retry → waiting %.2fs", wait)
                await self._sleep_with_jitter(wait)
            attempt += 1

        if last_exc:
            logger.error("LLM Structured Request Failed → all retries exhausted: %s", str(last_exc))
            raise last_exc
        raise RuntimeError("Не удалось выполнить responses.parse без исключения, но и без результата")
    
    async def analyze_relevance(self, message_text: str, prompt: str) -> bool:
        """
        Анализирует релевантность сообщения для суммаризации.
        
        Args:
            message_text: Текст сообщения
            prompt: Промпт для анализа релевантности
        
        Returns:
            True если сообщение релевантно, False иначе
        """
        logger.debug("Analyze Relevance → message_len=%d", len(message_text))
        try:
            response = await self._chat_completion_with_retries(
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": f"Сообщение: {message_text}\n\nНужно ли это сообщение для суммаризации? Ответь только 'да' или 'нет'."}
                ],
                temperature=0.3,
                max_tokens=10,
            )
            answer = response.choices[0].message.content.strip().lower()
            is_relevant = answer.startswith("да")
            logger.debug("Analyze Relevance ← result=%s answer=%s", is_relevant, answer)
            return is_relevant
        except Exception as e:
            # В случае ошибки считаем сообщение релевантным
            logger.error("Analyze Relevance → error=%s, defaulting to True", str(e))
            return True
    
    async def _load_prompts_if_needed(self) -> None:
        """Загружает промпты из файлов, если они еще не загружены (ленивая загрузка)."""
        if self._structured_system_prompt_template is None:
            self._structured_system_prompt_template = await load_prompt("structured_system_prompt.txt")
        
        if self._structured_user_prompt_template is None:
            self._structured_user_prompt_template = await load_prompt("structured_user_prompt.txt")
        
        if self._fallback_user_prompt_template is None:
            self._fallback_user_prompt_template = await load_prompt("fallback_user_prompt.txt")
    
    async def summarize_messages(
        self, 
        messages: List[Dict], 
        prompt: str,
        structured_system_prompt: Optional[str] = None,
        max_output_tokens: Optional[int] = None
    ) -> List[Dict]:
        """
        Суммаризирует сообщения по темам.
        
        Args:
            messages: Список сообщений [{"text": "...", "message_id": 123}]
            prompt: Промпт для суммаризации
            structured_system_prompt: Опциональный кастомный structured_system_prompt.
                                     Если не указан, используется стандартный из файла.
        
        Returns:
            Список тем [{"topic": "...", "message_ids": [1,2,3]}]
        """
        # Формируем список сообщений для промпта с реальными message_id
        messages_text = ""
        for msg in messages:
            try:
                message_id = msg.get("message_id")
                text = msg.get("text", "")
                username = msg.get("username")
                user_id = msg.get("user_id")
                first_name = msg.get("first_name")
                last_name = msg.get("last_name")
                forward_id = msg.get("forward_id")

                # Сборка авторской подписи
                name_parts = []
                if first_name:
                    name_parts.append(first_name)
                if last_name:
                    name_parts.append(last_name)
                full_name = " ".join(name_parts).strip()
                handle = f"@{username}" if username else ""
                id_part = f"id={user_id}" if user_id else ""
                author_parts = [p for p in [full_name, handle, id_part] if p]
                author_str = " ".join(author_parts)
                author_segment = f" author: {author_str}" if author_str else ""

                # Связь сообщения: ответ/пересылка
                ref_segment = f" ref_id: {forward_id}" if forward_id else ""

                messages_text += f"id:[{message_id}]{author_segment}{ref_segment} text: {text}\n"
            except Exception:
                # На всякий случай — минимальный формат
                messages_text += f"id:[{msg.get('message_id')}] text: {msg.get('text','')}\n"
        
        logger.info("Summarize Messages → messages_count=%d prompt_len=%d", len(messages), len(prompt))
        
        # Загружаем промпты из файлов (если еще не загружены)
        await self._load_prompts_if_needed()
        
        # Используем кастомный structured_system_prompt, если передан, иначе стандартный
        structured_system_prompt_to_use = structured_system_prompt if structured_system_prompt is not None else self._structured_system_prompt_template
        
        # Пытаемся сделать Structured Output (предпочтительный путь)
        try:
            # Усиливаем системную инструкцию для структурированного вывода
            system_prompt = f"{prompt}\n\n{structured_system_prompt_to_use}"
            user_input = self._structured_user_prompt_template.format(messages_text=messages_text)
            # Используем значение из конфига, если указано и > 0, иначе None (без ограничения)
            # Если max_output_tokens не указан или равен 0, передаем None, чтобы не ограничивать ответ
            structured_max_tokens = max_output_tokens if (max_output_tokens is not None and max_output_tokens > 0) else None
            structured: TopicsResponse = await self._responses_parse_with_retries(
                system=system_prompt,
                input_text=user_input,
                temperature=0.3,
                max_output_tokens=structured_max_tokens,
            )
            # Приводим к ожидаемому интерфейсу [{topic, topic_description, message_ids, message_count, participants}, ...]
            # Модель уже гарантирует только 1 message_id (самое раннее сообщение темы)
            result = []
            for item in (structured.topics or []):
                if not item.topic:
                    continue
                
                # Преобразуем UserItem объекты в словари
                participants_dicts = []
                for participant in (item.participants or []):
                    participants_dicts.append({
                        "username": participant.username,
                        "first_name": participant.first_name,
                        "second_name": participant.second_name,
                        "message_count": participant.message_count
                    })
                
                result.append({
                    "topic": item.topic,
                    "topic_description": getattr(item, "topic_description", ""),
                    "message_ids": (item.message_ids if item.message_ids else []),
                    "message_count": getattr(item, "message_count", 1),
                    "participants": participants_dicts
                })
            logger.info("Summarize Messages ← topics_count=%d (structured output)", len(result))
            # Сортируем топики по популярности (по количеству сообщений, по убыванию)
            result.sort(key=lambda x: x.get("message_count", 1), reverse=True)
            return result
        except Exception as structured_err:
            logger.warning("Summarize Messages → structured output failed, falling back: %s", str(structured_err))
            # Фолбэк: старый путь через chat.completions и парсинг текста
            try:
                user_content = self._fallback_user_prompt_template.format(messages_text=messages_text)
                # Используем значение из конфига, если указано и > 0, иначе None (без ограничения)
                # Если max_output_tokens не указан или равен 0, передаем None, чтобы не ограничивать ответ
                fallback_max_tokens = max_output_tokens if (max_output_tokens is not None and max_output_tokens > 0) else None
                response = await self._chat_completion_with_retries(
                    messages=[
                        {"role": "system", "content": prompt},
                        {
                            "role": "user",
                            "content": user_content,
                        },
                    ],
                    temperature=0.7,
                    max_tokens=fallback_max_tokens,
                )
                summary_text = response.choices[0].message.content.strip()
                parsed = self._parse_summary(summary_text, messages)
                # Оставляем только первое сообщение темы (самое раннее) и добавляем topic_description если его нет
                for t in parsed:
                    ids = t.get("message_ids") or []
                    # Сохраняем количество сообщений до обрезки
                    original_count = len(ids) if ids else 1
                    # Берем только первый id (самое раннее сообщение темы)
                    t["message_ids"] = ids[:1] if ids else []
                    # Если topic_description отсутствует, используем пустую строку (или можно topic как fallback)
                    if "topic_description" not in t:
                        t["topic_description"] = ""
                    # Добавляем message_count (используем количество найденных message_ids или 1 по умолчанию)
                    t["message_count"] = original_count if original_count > 0 else 1
                    # Для fallback парсинга participants будет пустым списком (LLM не возвращает их в текстовом формате)
                    if "participants" not in t:
                        t["participants"] = []
                logger.info("Summarize Messages ← topics_count=%d (fallback parsing)", len(parsed))
                # Сортируем топики по популярности (по количеству сообщений, по убыванию)
                parsed.sort(key=lambda x: x.get("message_count", 1), reverse=True)
                return parsed
            except Exception as fallback_err:
                logger.error("Summarize Messages → fallback also failed: structured_err=%s fallback_err=%s", 
                           str(structured_err), str(fallback_err))
                return []
    
    def _parse_summary(self, summary_text: str, messages: List[Dict]) -> List[Dict]:
        """
        Парсит результат суммаризации и извлекает номера сообщений.
        
        Args:
            summary_text: Текст суммаризации от ИИ
            messages: Исходный список сообщений
        
        Returns:
            Список тем с номерами сообщений и описаниями
        """
        topics = []
        lines = summary_text.split("\n")
        
        current_topic = None
        current_topic_description = None
        current_message_ids = []
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            # Ищем строки, начинающиеся с "-" или цифры
            if line.startswith("-") or (line and line[0].isdigit()):
                # Сохраняем предыдущую тему
                if current_topic:
                    topics.append({
                        "topic": current_topic,
                        "topic_description": current_topic_description or "",
                        "message_ids": current_message_ids
                    })
                
                # Извлекаем тему, описание и номера сообщений
                topic_text = line.lstrip("- ").strip()
                
                # Пытаемся распарсить формат "Заголовок. Описание (id)" или "Заголовок (id)"
                # Ищем номера в скобках (это реальные message_id)
                numbers = re.findall(r'\((\d+(?:,\s*\d+)*)\)', topic_text)
                message_ids = []
                
                if numbers:
                    # Берем последние найденные номера (это message_id)
                    last_numbers = numbers[-1]
                    message_ids = [int(n.strip()) for n in last_numbers.split(",")]
                    # Убираем номера из текста темы
                    topic_text = re.sub(r'\s*\(\d+(?:,\s*\d+)*\)\s*$', '', topic_text).strip()
                else:
                    # Если номера не найдены в скобках, ищем их в квадратных скобках
                    square_brackets = re.findall(r'\[(\d+(?:,\s*\d+)*)\]', topic_text)
                    if square_brackets:
                        last_numbers = square_brackets[-1]
                        message_ids = [int(n.strip()) for n in last_numbers.split(",")]
                        topic_text = re.sub(r'\s*\[\d+(?:,\s*\d+)*\]\s*', '', topic_text).strip()
                
                # Пытаемся разделить на заголовок и описание (формат "Заголовок. Описание")
                parts = topic_text.split(".", 1)
                if len(parts) == 2 and len(parts[0].split()) <= 5:  # Заголовок обычно короткий
                    current_topic = parts[0].strip()
                    current_topic_description = parts[1].strip()
                else:
                    # Если не удалось разделить, используем весь текст как заголовок
                    current_topic = topic_text
                    current_topic_description = None
                
                current_message_ids = message_ids
        
        # Сохраняем последнюю тему
        if current_topic:
            topics.append({
                "topic": current_topic,
                "topic_description": current_topic_description or "",
                "message_ids": current_message_ids
            })
        
        # Если не удалось распарсить, создаем одну тему со всеми сообщениями
        if not topics:
            all_ids = [msg["message_id"] for msg in messages]
            first_line = summary_text.split("\n")[0] if summary_text else "Обсуждение"
            topics.append({
                "topic": first_line,
                "topic_description": "",
                "message_ids": all_ids
            })
        
        return topics

