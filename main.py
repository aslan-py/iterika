import argparse
import asyncio

from loguru import logger

from src.logger import setup_logging
from src.normalizer.normalize import normalize_many, save_normalized
from src.parser.wb import fetch_products, save_raw


async def _run(fresh: bool) -> None:
    setup_logging()

    # Этап 1 — парсинг
    raw_products = await fetch_products(fresh=fresh)
    if not raw_products:
        logger.warning('Товары не собраны — прерываем')
        return

    save_raw(raw_products)

    # Этап 2 — нормализация
    products = normalize_many(raw_products)
    save_normalized(products)
    logger.info('Готово: {} нормализованных товаров', len(products))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Парсер Wildberries')
    parser.add_argument(
        '--fresh',
        action='store_true',
        help='начать сбор заново, игнорируя checkpoint',
    )
    args = parser.parse_args()
    asyncio.run(_run(args.fresh))
