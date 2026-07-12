"""Точка входа пайплайна аналитики маркетплейсов.

Режимы запуска:
  uv run main.py                  весь пайплайн (продолжает с checkpoint)
  uv run main.py --fresh          весь пайплайн с нуля
  uv run main.py --parsing        только парсинг
  uv run main.py --parsing --fresh  только парсинг с нуля
  uv run main.py --normalizer     только нормализация (последний wb_raw)
  uv run main.py --llm            только LLM (последний normalized)
  uv run main.py --crm            только CRM (последний classified)
"""
import argparse
import asyncio

from loguru import logger

from src.crm.amocrm import create_tasks
from src.crm.selector import select_interesting
from src.llm.classifier import classify_all
from src.logger import setup_logging
from src.normalizer.normalize import normalize_many, save_normalized
from src.parser.wb import fetch_products, save_raw
from src.redis_client import (
    get_created_task_ids,
    make_redis,
    mark_tasks_created,
)
from src.storage import load_latest_products, load_latest_raw


async def stage_parse(fresh: bool) -> list[dict]:
    """Этап 1 — парсинг WB. Возвращает сырые товары (может быть пусто)."""
    raw_products = await fetch_products(fresh=fresh)
    if not raw_products:
        logger.warning('Товары не собраны')
        return []
    save_raw(raw_products)
    return raw_products


def stage_normalize(raw_products: list[dict] | None = None) -> list:
    """Этап 2 — нормализация. Без аргумента берёт последний wb_raw."""
    if raw_products is None:
        raw_products = load_latest_raw()
        if raw_products is None:
            logger.error(
                'Нет файла парсинга для нормализации — '
                'сначала запустите парсинг (--parsing)'
            )
            return []
    products = normalize_many(raw_products)
    save_normalized(products)
    return products


async def stage_llm(products: list | None = None) -> list:
    """Этап 3 — LLM-классификация. Без аргумента берёт последний normalized."""
    if products is None:
        products = load_latest_products('normalized')
        if products is None:
            logger.error(
                'Нет нормализованного файла — '
                'сначала запустите нормализацию (--normalizer)'
            )
            return []
    classified = await classify_all(products)
    save_normalized(classified, filename_prefix='classified')
    return classified


async def stage_crm(products: list | None = None) -> list:
    """Этап 4 — задачи в CRM. Без аргумента берёт последний classified.

    Через Redis запоминает id товаров с уже созданными задачами, чтобы
    каждый запуск брал следующие позиции по приоритету (Премиум →
    Стандарт → Эконом), а не дублировал предыдущие.
    """
    if products is None:
        products = load_latest_products('classified')
        if products is None:
            logger.error(
                'Нет финального classified-файла с сегментами — '
                'сначала запустите классификацию (--llm)'
            )
            return []

    redis = await make_redis()
    exclude = await get_created_task_ids(redis) if redis else set()

    interesting = select_interesting(products, exclude_ids=exclude)
    if not interesting:
        logger.info('Новых позиций для CRM нет — все уже отправлены')
        return []

    tasks = await create_tasks(interesting)
    if redis:
        await mark_tasks_created(redis, [p.id for p in interesting])
    return tasks


async def run_full(fresh: bool) -> None:
    """Полный пайплайн: результаты передаются между этапами напрямую."""
    raw = await stage_parse(fresh)
    if not raw:
        return
    products = stage_normalize(raw)
    classified = await stage_llm(products)
    tasks = await stage_crm(classified)

    logger.info(
        'Готово: {} товаров, {} с сегментом, {} задач в CRM',
        len(classified),
        sum(1 for p in classified if p.segment is not None),
        len(tasks),
    )


async def _dispatch(args: argparse.Namespace) -> None:
    """Выбирает: один этап или полный пайплайн."""
    setup_logging()

    if args.parsing:
        await stage_parse(args.fresh)
    elif args.normalizer:
        stage_normalize()
    elif args.llm:
        await stage_llm()
    elif args.crm:
        await stage_crm()
    else:
        await run_full(args.fresh)


def _parse_args() -> argparse.Namespace:
    """Разбирает аргументы CLI."""
    parser = argparse.ArgumentParser(
        description='Пайплайн аналитики маркетплейсов'
    )
    parser.add_argument(
        '--fresh',
        action='store_true',
        help='начать сбор заново, игнорируя checkpoint',
    )
    stage = parser.add_mutually_exclusive_group()
    stage.add_argument(
        '--parsing', action='store_true', help='только парсинг'
    )
    stage.add_argument(
        '--normalizer', action='store_true', help='только нормализация'
    )
    stage.add_argument(
        '--llm', action='store_true', help='только LLM-классификация'
    )
    stage.add_argument(
        '--crm', action='store_true', help='только задачи в CRM'
    )
    return parser.parse_args()


if __name__ == '__main__':
    asyncio.run(_dispatch(_parse_args()))
