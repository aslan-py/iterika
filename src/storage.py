"""Загрузка и именование файлов пайплайна в output/.

Имена файлов включают категорию (slug), чтобы разные категории
(смартфоны / кроссовки / удочки) не перезаписывали друг друга и
чтобы CRM можно было запускать по конкретной категории.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from loguru import logger

from src.config import settings
from src.models import Product


def slugify(text: str) -> str:
    """Преобразует категорию в безопасный фрагмент имени файла.

    Пробелы → подчёркивания, регистр в нижний, спецсимволы убраны.
    Кириллица сохраняется ('смартфон apple' → 'смартфон_apple').
    """
    cleaned = re.sub(r'[^\w-]', '_', text.strip().lower())
    return re.sub(r'_+', '_', cleaned).strip('_')


def build_filename(prefix: str, category: str) -> str:
    """Собирает имя файла вида `{prefix}_{категория}_{время}.json`."""
    from datetime import datetime, timezone

    ts = datetime.now(timezone.utc).strftime('%Y-%m-%d_%H-%M-%S')
    return f'{prefix}_{slugify(category)}_{ts}.json'


def _latest_file(
    prefix: str, category: str = '', output_dir: str = ''
) -> Path | None:
    """Возвращает самый свежий файл по времени создания или None.

    category — если задана, ищет только файлы этой категории;
    иначе берёт последний файл любого `{prefix}_*`.
    Сортировка по mtime, чтобы категория в имени не сбивала порядок.
    """
    out = Path(output_dir or settings.output_dir)
    if not out.exists():
        return None
    pattern = (
        f'{prefix}_{slugify(category)}_*.json'
        if category
        else f'{prefix}_*.json'
    )
    files = sorted(out.glob(pattern), key=lambda p: p.stat().st_mtime)
    return files[-1] if files else None


def load_latest_raw(
    category: str = '', output_dir: str = ''
) -> list[dict[str, Any]] | None:
    """Загружает последний файл сырого парсинга (wb_raw_*.json)."""
    path = _latest_file('wb_raw', category, output_dir)
    if path is None:
        return None
    logger.info('Загружаю сырой файл: {}', path)
    return json.loads(path.read_text(encoding='utf-8'))


def load_latest_products(
    prefix: str, category: str = '', output_dir: str = ''
) -> list[Product] | None:
    """Загружает последний файл товаров (normalized_* / classified_*).

    Десериализует JSON обратно в объекты Product. None если файла нет.
    """
    path = _latest_file(prefix, category, output_dir)
    if path is None:
        return None
    logger.info('Загружаю файл: {}', path)
    data = json.loads(path.read_text(encoding='utf-8'))
    return [Product(**item) for item in data]
