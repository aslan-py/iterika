import asyncio
import json
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from loguru import logger
from tenacity import (
    RetryCallState,
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from src.config import settings
from src.parser import checkpoint
from src.parser.constants import WB_HEADERS, WB_SEARCH_URL


def _is_rate_limited(exc: BaseException) -> bool:
    return (
        isinstance(exc, httpx.HTTPStatusError)
        and exc.response.status_code == 429
    )


def _log_retry(state: RetryCallState) -> None:
    logger.warning(
        'Rate limit 429 — попытка {}, ждём перед повтором...',
        state.attempt_number,
    )


@retry(
    retry=retry_if_exception(_is_rate_limited),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    stop=stop_after_attempt(4),
    before_sleep=_log_retry,
    reraise=True,
)
async def _fetch_page(
    client: httpx.AsyncClient,
    page: int,
) -> list[dict[str, Any]]:
    """Загружает одну страницу поиска Wildberries.

    При получении ответа 429 tenacity повторяет запрос
    с экспоненциальной задержкой: 1 → 2 → 4 → 8 секунд (до 4 попыток).
    """
    params: dict[str, str] = {
        'appType': '1',
        'curr': 'rub',
        'dest': '-1257786',
        'query': settings.wb_query,
        'resultset': 'catalog',
        'sort': 'popular',
        'spp': '30',
        'page': str(page),
    }
    response = await client.get(WB_SEARCH_URL, params=params, timeout=30)
    response.raise_for_status()
    data: dict[str, Any] = response.json()
    return data.get('products', [])


async def fetch_products(fresh: bool = False) -> list[dict[str, Any]]:
    """Собирает сырые товары с Wildberries постранично.

    При fresh=False проверяет наличие checkpoint и продолжает с последней
    сохранённой страницы. При fresh=True удаляет checkpoint и начинает заново.

    При любой ошибке сохраняет прогресс в checkpoint и возвращает уже
    собранные данные. Следующий запуск продолжит с той же страницы.

    В DEMO_MODE собирает не более DEMO_LIMIT товаров вместо WB_MAX_PRODUCTS.
    """
    limit = (
        settings.demo_limit
        if settings.demo_mode
        else settings.wb_max_products
    )

    if fresh:
        checkpoint.delete()

    resumed = checkpoint.load(settings.wb_query)
    if resumed:
        page, collected = resumed
    else:
        page, collected = 1, []

    async with httpx.AsyncClient(headers=WB_HEADERS) as client:
        while len(collected) < limit:
            try:
                products = await _fetch_page(client, page)
            except Exception as exc:
                logger.error(
                    'Страница {} недоступна: {} — завершаем gracefully',
                    page,
                    exc,
                )
                break

            if not products:
                logger.info('Страница {} пуста — конец каталога', page)
                checkpoint.delete()
                break

            remaining = limit - len(collected)
            batch = products[:remaining]
            collected.extend(batch)
            logger.info(
                'Страница {}: +{} товаров (итого {} / {})',
                page,
                len(batch),
                len(collected),
                limit,
            )

            checkpoint.save(page + 1, settings.wb_query, collected)

            if len(collected) >= limit:
                break

            page += 1
            delay = random.uniform(settings.wb_delay_min, settings.wb_delay_max)
            logger.debug('Пауза {:.1f}с перед страницей {}', delay, page)
            await asyncio.sleep(delay)

    if len(collected) >= limit:
        checkpoint.delete()

    return collected


def save_raw(
    products: list[dict[str, Any]],
    output_dir: str = '',
) -> Path:
    """Сохраняет список сырых товаров WB в JSON-файл с меткой времени UTC."""
    out = Path(output_dir or settings.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')
    path = out / f'wb_raw_{ts}.json'
    path.write_text(
        json.dumps(products, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )
    logger.info('Сохранено {} товаров → {}', len(products), path)
    return path
