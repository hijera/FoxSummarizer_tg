"""Сервис для работы с WhisperX для распознавания речи."""
import asyncio
import whisperx
from pathlib import Path
from typing import Optional
from concurrent.futures import ThreadPoolExecutor


class WhisperService:
    """Сервис для транскрипции аудио через WhisperX."""
    
    def __init__(self):
        """Инициализация сервиса."""
        self.model = None
        self.device = "cpu"  # Можно изменить на "cuda" если есть GPU
        self.compute_type = "int8"  # Можно изменить на "float16" для лучшего качества
        self.executor = ThreadPoolExecutor(max_workers=1)
    
    def _load_model(self):
        """Ленивая загрузка модели WhisperX."""
        if self.model is None:
            try:
                self.model = whisperx.load_model("base", device=self.device, compute_type=self.compute_type)
            except Exception as e:
                print(f"Ошибка загрузки модели WhisperX: {e}")
                raise
    
    def _transcribe_sync(self, audio_path: str) -> Optional[str]:
        """Синхронная транскрипция аудио (выполняется в executor)."""
        try:
            self._load_model()
            
            # Загружаем аудио и транскрибируем
            audio = whisperx.load_audio(audio_path)
            result = self.model.transcribe(audio, batch_size=16)
            
            # Извлекаем текст из результата
            # WhisperX возвращает словарь с ключом "segments"
            if isinstance(result, dict):
                # Если есть сегменты, объединяем их текст
                if "segments" in result and result["segments"]:
                    text_parts = []
                    for segment in result["segments"]:
                        if isinstance(segment, dict) and "text" in segment:
                            text_parts.append(segment["text"].strip())
                    if text_parts:
                        return " ".join(text_parts).strip()
                
                # Если есть прямой текст
                if "text" in result:
                    text = result["text"]
                    if isinstance(text, str) and text.strip():
                        return text.strip()
            
            return None
        except Exception as e:
            print(f"Ошибка при транскрипции аудио: {e}")
            return None
    
    async def transcribe_audio(self, audio_path: str) -> Optional[str]:
        """
        Транскрибирует аудиофайл в текст.
        
        Args:
            audio_path: Путь к аудиофайлу
        
        Returns:
            Транскрибированный текст или None в случае ошибки
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self.executor, self._transcribe_sync, audio_path)
    
    async def download_and_transcribe(
        self, 
        bot, 
        file_id: str, 
        chat_id: int
    ) -> Optional[str]:
        """
        Скачивает аудиофайл из Telegram и транскрибирует его.
        
        Args:
            bot: Экземпляр бота aiogram
            file_id: ID файла в Telegram
            chat_id: ID чата
        
        Returns:
            Транскрибированный текст или None
        """
        try:
            # Получаем информацию о файле
            file = await bot.get_file(file_id)
            
            # Создаем временную директорию
            temp_dir = Path("temp_audio")
            temp_dir.mkdir(exist_ok=True)
            
            # Скачиваем файл
            audio_path = temp_dir / f"{file_id}.ogg"
            await bot.download_file(file.file_path, destination=audio_path)
            
            # Транскрибируем
            text = await self.transcribe_audio(str(audio_path))
            
            # Удаляем временный файл
            try:
                audio_path.unlink()
            except:
                pass
            
            return text
        except Exception as e:
            print(f"Ошибка при скачивании и транскрипции аудио: {e}")
            return None
