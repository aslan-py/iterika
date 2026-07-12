"""Redis-клиент для кэширования сегментов товаров."""
from __future__ import annotations

import redis.asyncio as aioredis
from loguru import logger

from src.config import settings

# v2: ключ кэша — нормализованное название товара (не ID).
# Одна модель = один ключ = один сегмент для всех продавцов.
_KEY_PREFIX = 'seg:v2:'


async def make_redis() -> aioredis.Redis | None:
    """Создаёт Redis-клиент; None если URL не задан или Redis недоступен."""
    if not settings.redis_url:
        return None
    try:
        client: aioredis.Redis = aioredis.from_url(
            settings.redis_url,
            decode_responses=True,
        )
        await client.ping()
        logger.info('Redis подключён: {}', settings.redis_url)
        return client
    except Exception as exc:
        logger.warning('Redis недоступен ({}), кэш отключён', exc)
        return None


async def get_segment(
    client: aioredis.Redis, key: str
) -> str | None:
    """Возвращает закэшированный сегмент по ключу или None.

    key — нормализованное название товара.
    """
    return await client.get(f'{_KEY_PREFIX}{key}')


async def store_segments(
    client: aioredis.Redis,
    segments: dict[str, str],
) -> None:
    """Сохраняет словарь {ключ: segment} в Redis одним pipeline.

    Ключ — нормализованное название товара.
    """
    if not segments:
        return
    pipe = client.pipeline()
    for key, segment in segments.items():
        pipe.set(
            f'{_KEY_PREFIX}{key}',
            segment,
            ex=settings.redis_cache_ttl,
        )
    await pipe.execute()
    logger.debug('Redis: сохранено {} сегментов', len(segments))
