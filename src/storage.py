"""Загрузка последних сохранённых файлов пайплайна из output/.

Используется при поэтапном запуске (--normalizer, --llm, --crm),
когда этап берёт результат предыдущего не из памяти, а с диска.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from loguru import logger

from src.config import settings
from src.models import Product


def _latest_file(prefix: str, output_dir: str = '') -> Path | None:
    """Возвращает самый свежий файл `{prefix}_*.json` или None.

    Файлы именуются по шаблону времени `%Y-%m-%d_%H-%M`, поэтому
    лексикографическая сортировка совпадает с хронологической.
    """
    out = Path(output_dir or settings.output_dir)
    if not out.exists():
        return None
    files = sorted(out.glob(f'{prefix}_*.json'))
    return files[-1] if files else None


def load_latest_raw(output_dir: str = '') -> list[dict[str, Any]] | None:
    """Загружает последний файл сырого парсинга (wb_raw_*.json)."""
    path = _latest_file('wb_raw', output_dir)
    if path is None:
        return None
    logger.info('Загружаю сырой файл: {}', path)
    return json.loads(path.read_text(encoding='utf-8'))


def load_latest_products(
    prefix: str, output_dir: str = ''
) -> list[Product] | None:
    """Загружает последний файл товаров (normalized_* / classified_*).

    Десериализует JSON обратно в объекты Product. None если файла нет.
    """
    path = _latest_file(prefix, output_dir)
    if path is None:
        return None
    logger.info('Загружаю файл: {}', path)
    data = json.loads(path.read_text(encoding='utf-8'))
    return [Product(**item) for item in data]
