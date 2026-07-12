"""Интеграция с AmoCRM: создание задач менеджерам.

При отсутствии токена или ошибке API задачи сохраняются в
output/tasks.json (graceful fallback) — пайплайн не падает.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from loguru import logger

from src.config import settings
from src.models import Product

# Срок выполнения задачи по умолчанию — 3 дня от момента создания.
_TASK_DEADLINE_DAYS = 1
# Тип задачи в AmoCRM: 1 — «Связаться» (стандартный системный тип).
_TASK_TYPE_ID = 1


def _api_url() -> str:
    """URL эндпоинта задач AmoCRM для текущего поддомена."""
    return f'https://{settings.amocrm_subdomain}.amocrm.ru/api/v4/tasks'


def _build_task(product: Product) -> dict[str, Any]:
    """Формирует тело задачи AmoCRM из товара."""
    deadline = int(time.time()) + _TASK_DEADLINE_DAYS * 24 * 3600
    text = (
        f'Проработать позицию [{product.segment}]: {product.name}. '
        f'Цена {product.price:.0f} {product.currency}. '
        f'Ссылка: {product.url}'
    )
    return {
        'text': text,
        'complete_till': deadline,
        'task_type_id': _TASK_TYPE_ID,
    }


def _save_fallback(tasks: list[dict[str, Any]], output_dir: str = '') -> Path:
    """Сохраняет задачи в output/tasks.json при недоступности CRM."""
    out = Path(output_dir or settings.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime('%Y-%m-%d_%H-%M')
    path = out / f'tasks_{ts}.json'
    path.write_text(
        json.dumps(tasks, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )
    logger.info('Задачи сохранены в fallback-файл → {}', path)
    return path


async def _post_tasks(tasks: list[dict[str, Any]]) -> bool:
    """Отправляет задачи в AmoCRM. True при успехе, False при ошибке."""
    headers = {
        'Authorization': f'Bearer {settings.amocrm_access_token}',
        'Content-Type': 'application/json',
    }
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                _api_url(), headers=headers, json=tasks, timeout=30
            )
            response.raise_for_status()
    except Exception as exc:
        logger.error('AmoCRM недоступен ({}) — fallback в JSON', exc)
        return False

    logger.info('AmoCRM: создано {} задач', len(tasks))
    return True


async def create_tasks(products: list[Product]) -> list[dict[str, Any]]:
    """Создаёт задачи в AmoCRM по отобранным товарам.

    Без токена или при ошибке API — сохраняет задачи в output/tasks.json.
    Возвращает список сформированных задач (для отчёта).
    """
    if not products:
        logger.warning('Нет позиций для задач CRM')
        return []

    tasks = [_build_task(p) for p in products]

    if not settings.amocrm_access_token:
        logger.warning('AMOCRM_ACCESS_TOKEN пуст — сохраняем задачи в JSON')
        _save_fallback(tasks)
        return tasks

    ok = await _post_tasks(tasks)
    if not ok:
        _save_fallback(tasks)
    return tasks
