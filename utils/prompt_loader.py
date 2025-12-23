"""Утилита для загрузки промптов из файлов."""
import aiofiles
from pathlib import Path


async def load_prompt(filename: str) -> str:
    """
    Загружает промпт из файла.
    
    Args:
        filename: Имя файла промпта (например, 'summarization_prompt.txt')
                  Или путь относительно корня проекта (например, 'prompts/custom_prompt.txt')
    
    Returns:
        Содержимое файла как строка
    """
    prompts_dir = Path(__file__).parent.parent / "prompts"
    prompt_path = prompts_dir / filename
    
    if not prompt_path.exists():
        raise FileNotFoundError(f"Промпт не найден: {prompt_path}")
    
    async with aiofiles.open(prompt_path, mode='r', encoding='utf-8') as f:
        content = await f.read()
    
    return content.strip()


async def load_prompt_by_path(prompt_path: str) -> str:
    """
    Загружает промпт из файла по пути относительно корня проекта.
    
    Args:
        prompt_path: Путь к файлу промпта относительно корня проекта
                     (например, 'prompts/custom_summarization_prompt.txt')
    
    Returns:
        Содержимое файла как строка
    
    Raises:
        FileNotFoundError: Если файл не найден
    """
    project_root = Path(__file__).parent.parent
    full_path = project_root / prompt_path
    
    if not full_path.exists():
        raise FileNotFoundError(f"Промпт не найден: {full_path}")
    
    async with aiofiles.open(full_path, mode='r', encoding='utf-8') as f:
        content = await f.read()
    
    return content.strip()

