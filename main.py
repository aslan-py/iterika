import argparse
import asyncio

from loguru import logger

from src.crm.amocrm import create_tasks
from src.crm.selector import select_interesting
from src.llm.classifier import classify_all
from src.logger import setup_logging
from src.normalizer.normalize import normalize_many, save_normalized
from src.parser.wb import fetch_products, save_raw


async def _run(fresh: bool) -> None:
    """Запускает полный пайплайн: парсинг → нормализация → LLM → отчёт."""
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

    # Этап 3 — LLM-классификация
    products_classified = await classify_all(products)
    save_normalized(products_classified, filename_prefix='classified')

    # Этап 4 — отбор интересных позиций и задачи в CRM
    interesting = select_interesting(products_classified)
    tasks = await create_tasks(interesting)

    logger.info(
        'Готово: {} товаров, {} с сегментом, {} задач в CRM',
        len(products_classified),
        sum(1 for p in products_classified if p.segment is not None),
        len(tasks),
    )


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Парсер Wildberries')
    parser.add_argument(
        '--fresh',
        action='store_true',
        help='начать сбор заново, игнорируя checkpoint',
    )
    args = parser.parse_args()
    asyncio.run(_run(args.fresh))
