import argparse
import asyncio

from src.logger import setup_logging
from src.parser.wb import fetch_products, save_raw


async def _run(fresh: bool) -> None:
    setup_logging()
    products = await fetch_products(fresh=fresh)
    if products:
        save_raw(products)
    else:
        from loguru import logger
        logger.warning('Товары не собраны — файл не сохранён')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Парсер Wildberries')
    parser.add_argument(
        '--fresh',
        action='store_true',
        help='начать сбор заново, игнорируя checkpoint',
    )
    args = parser.parse_args()
    asyncio.run(_run(args.fresh))
