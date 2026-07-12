"""LLM-классификатор ценовых сегментов (DeepSeek) с Redis-кэшем.

Классификация — гибридная: база по цене (перцентили выборки),
но узнаваемые флагманские модели поднимаются в Премиум даже при
низкой цене. Товары с одинаковым названием классифицируются один
раз (дедупликация по названию), что кратно снижает нагрузку на LLM.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import Any

import redis.asyncio as aioredis
from loguru import logger
from openai import APIConnectionError, AsyncOpenAI, RateLimitError
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from src.config import settings
from src.llm.prompts import (
    _CLASSIFICATION_RULES,
    _PROMPT_TEMPLATE,
    _SYSTEM,
)
from src.models import Product, Segment
from src.redis_client import get_segment, make_redis, store_segments

_VALID = {s.value for s in Segment}


@dataclass
class PriceStats:
    """Статистика цен по всей выборке товаров."""

    min: float
    p_low: float    # граница Эконом / Стандарт (SEGMENT_P_ECONOMY)
    median: float
    p_high: float   # граница Стандарт / Премиум (SEGMENT_P_PREMIUM)
    max: float


def _name_key(name: str) -> str:
    """Нормализует название в ключ дедупликации и кэша.

    Приводит к нижнему регистру и схлопывает пробелы, чтобы
    'iPhone 13  128Gb' и 'iphone 13 128gb' считались одним товаром.
    """
    return re.sub(r'\s+', ' ', name.strip().lower())


def _compute_stats(products: list[Product]) -> PriceStats:
    """Вычисляет ценовые перцентили по полному списку товаров.

    Пороги берутся из settings.segment_p_economy / segment_p_premium,
    чтобы их можно было настраивать через .env без правки кода.
    """
    prices = sorted(p.price for p in products)
    n = len(prices)
    return PriceStats(
        min=prices[0],
        p_low=prices[int((n - 1) * settings.segment_p_economy)],
        median=prices[n // 2],
        p_high=prices[int((n - 1) * settings.segment_p_premium)],
        max=prices[-1],
    )


def _representative(group: list[Product]) -> Product:
    """Выбирает представителя группы одинаковых товаров.

    Берёт товар с медианной ценой, чтобы цена, уходящая в LLM,
    была типичной для этой модели (устойчива к выбросам продавцов).
    """
    ordered = sorted(group, key=lambda p: p.price)
    return ordered[len(ordered) // 2]


def _build_prompt(products: list[Product]) -> str:
    """Строит промпт из шаблона: правила и список товаров (с брендом)."""
    category = products[0].category if products else 'товары'
    items = [
        {
            'index': i,
            'name': p.name,
            'price_rub': p.price,
            'brand': p.description,
        }
        for i, p in enumerate(products)
    ]
    body = json.dumps(items, ensure_ascii=False, indent=2)
    return _PROMPT_TEMPLATE.format(
        category=category,
        rules=_CLASSIFICATION_RULES,
        body=body,
        count=len(products),
    )


def _fallback_by_price(
    products: list[Product], stats: PriceStats
) -> list[str]:
    """Классифицирует по глобальным перцентилям (fallback без LLM)."""
    result: list[str] = []
    for p in products:
        if p.price <= stats.p_low:
            result.append(Segment.ECONOMY.value)
        elif p.price <= stats.p_high:
            result.append(Segment.STANDARD.value)
        else:
            result.append(Segment.PREMIUM.value)
    return result


def _make_client() -> AsyncOpenAI | None:
    """Создаёт AsyncOpenAI-клиент; None если ключ не задан."""
    if not settings.deepseek_api_key:
        logger.warning('DEEPSEEK_API_KEY не задан — fallback по цене')
        return None
    return AsyncOpenAI(
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
    )


@retry(
    retry=retry_if_exception_type((APIConnectionError, RateLimitError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    reraise=True,
)
async def _call_llm(
    client: AsyncOpenAI,
    products: list[Product],
) -> list[str]:
    """Один запрос к DeepSeek; возвращает список сегментов."""
    response = await client.chat.completions.create(
        model=settings.deepseek_model,
        response_format={'type': 'json_object'},
        messages=[
            {'role': 'system', 'content': _SYSTEM},
            {'role': 'user', 'content': _build_prompt(products)},
        ],
        temperature=0.1,
    )
    content = response.choices[0].message.content or '{}'
    data: dict[str, Any] = json.loads(content)
    raw: list[Any] = data.get('segments', [])
    return [s if s in _VALID else Segment.STANDARD.value for s in raw]


async def _classify_batch(
    client: AsyncOpenAI,
    products: list[Product],
    stats: PriceStats,
) -> list[str]:
    """Классифицирует один батч; при ошибке — fallback по цене.

    Промпт LLM опирается на бренд (без ценовых границ). stats нужен
    только для ценового fallback, если LLM недоступен.
    """
    try:
        segments = await _call_llm(client, products)
    except Exception as exc:
        logger.warning('LLM ошибка ({}), fallback по цене', exc)
        return _fallback_by_price(products, stats)

    if len(segments) != len(products):
        logger.warning(
            'LLM вернул {} вместо {} сегментов, fallback',
            len(segments),
            len(products),
        )
        return _fallback_by_price(products, stats)

    return segments


def _group_by_name(products: list[Product]) -> dict[str, list[Product]]:
    """Группирует товары по нормализованному названию."""
    groups: dict[str, list[Product]] = {}
    for p in products:
        groups.setdefault(_name_key(p.name), []).append(p)
    return groups


async def _load_cache(
    redis: aioredis.Redis | None,
    keys: list[str],
) -> tuple[dict[str, str], list[str]]:
    """Читает сегменты из Redis; возвращает (найденные, не найденные)."""
    if redis is None:
        return {}, list(keys)
    cached: dict[str, str] = {}
    uncached: list[str] = []
    for key in keys:
        seg = await get_segment(redis, key)
        if seg:
            cached[key] = seg
        else:
            uncached.append(key)
    return cached, uncached


async def classify_all(
    products: list[Product], use_cache: bool = True
) -> list[Product]:
    """Классифицирует все товары с дедупликацией по названию и кэшем.

    Алгоритм:
    1. Группируем товары по названию — одинаковые модели в одну группу.
    2. Читаем Redis: известные названия не идут в LLM.
    3. Уникальные новые названия батчами отправляем в DeepSeek.
    4. После каждого батча пишем результат в Redis (устойчиво к сбою).
    5. Разносим сегменты обратно на все товары исходного списка.

    use_cache=False — игнорировать кэш чтения: все товары уходят в LLM
    заново (нужно после изменения правил классификации), результаты
    перезаписывают старый кэш.
    """
    if not products:
        return []

    stats = _compute_stats(products)
    logger.info(
        'Цены в выборке: мин={:.0f}, медиана={:.0f}, макс={:.0f} руб.',
        stats.min,
        stats.median,
        stats.max,
    )

    # --- Шаг 1: дедупликация по названию ---
    groups = _group_by_name(products)
    logger.info(
        'Дедупликация: {} товаров → {} уникальных названий',
        len(products),
        len(groups),
    )

    # --- Шаг 2: Redis-кэш по названию ---
    redis: aioredis.Redis | None = await make_redis()
    if use_cache:
        key_to_segment, uncached_keys = await _load_cache(
            redis, list(groups)
        )
    else:
        # Отладка промпта: игнорируем кэш чтения, всё в LLM заново
        key_to_segment, uncached_keys = {}, list(groups)
        logger.info('Кэш чтения отключён (--no-cache) — всё заново в LLM')

    if redis is not None:
        logger.info(
            'Redis кэш: {} названий из кэша, {} на классификацию',
            len(key_to_segment),
            len(uncached_keys),
        )

    # --- Шаг 3: LLM для новых названий (по представителю группы) ---
    reps = [_representative(groups[k]) for k in uncached_keys]
    llm_client = _make_client()
    batch_size = settings.llm_batch_size
    n_batches = math.ceil(len(reps) / batch_size) if reps else 0

    if n_batches:
        logger.info(
            'LLM-классификация: {} названий, {} батчей по {} шт.',
            len(reps),
            n_batches,
            batch_size,
        )

    llm_count = 0
    for i in range(n_batches):
        lo, hi = i * batch_size, (i + 1) * batch_size
        chunk = reps[lo:hi]
        chunk_keys = uncached_keys[lo:hi]
        segs = (
            await _classify_batch(llm_client, chunk, stats)
            if llm_client is not None
            else _fallback_by_price(chunk, stats)
        )
        batch_map = dict(zip(chunk_keys, segs, strict=False))
        key_to_segment.update(batch_map)
        llm_count += len(batch_map)

        # Шаг 4: пишем батч сразу — при сбое прогресс сохранится
        if redis is not None:
            await store_segments(redis, batch_map)

        logger.info('Батч {}/{} готов ({} шт.)', i + 1, n_batches, len(chunk))

    # --- Шаг 5: разносим сегменты обратно на все товары ---
    result = [
        p.model_copy(
            update={
                'segment': key_to_segment.get(
                    _name_key(p.name), Segment.STANDARD.value
                )
            }
        )
        for p in products
    ]

    logger.info(
        'Классификация завершена: {} товаров '
        '({} названий из кэша, {} через LLM)',
        len(result),
        len(key_to_segment) - llm_count,
        llm_count,
    )
    return result
