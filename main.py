"""Точка входа пайплайна аналитики маркетплейсов.

Режимы запуска:
  uv run main.py                  весь пайплайн (продолжает с checkpoint)
  uv run main.py --fresh          весь пайплайн с нуля
  uv run main.py --reset          очистить output/ и кэш Redis

Этапные флаги комбинируются, порядок не важен:
  uv run main.py --parsing                     только парсинг
  uv run main.py --parsing --fresh             парсинг с нуля
  uv run main.py --parsing --normalizer --llm  до classified, без CRM
  uv run main.py --normalizer --llm --crm      со среднего этапа до конца
  uv run main.py --llm                          последний normalized → LLM

Смежные этапы передают данные в памяти; при разрыве (пропущен средний
этап) данные берутся с последнего файла в output/. --reset выполняется
до этапов, поэтому `--reset --parsing --llm` = чистый прогон.
"""
import argparse
import asyncio
from pathlib import Path

from loguru import logger

from src.config import settings
from src.crm.amocrm import create_tasks
from src.crm.selector import select_interesting
from src.llm.classifier import classify_all
from src.logger import setup_logging
from src.normalizer.normalize import normalize_many, save_normalized
from src.parser.wb import fetch_products, save_raw
from src.redis_client import (
    flush_cache,
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


def stage_normalize(
    raw_products: list[dict] | None = None, category: str = ''
) -> list:
    """Этап 2 — нормализация. Без аргумента берёт последний wb_raw."""
    if raw_products is None:
        raw_products = load_latest_raw(category)
        if raw_products is None:
            logger.error(
                'Нет файла парсинга для нормализации — '
                'сначала запустите парсинг (--parsing)'
            )
            return []
    products = normalize_many(raw_products)
    save_normalized(products, category=category)
    return products


async def stage_llm(
    products: list | None = None, category: str = ''
) -> list:
    """Этап 3 — LLM-классификация. Без аргумента берёт последний normalized."""
    if products is None:
        products = load_latest_products('normalized', category)
        if products is None:
            logger.error(
                'Нет нормализованного файла — '
                'сначала запустите нормализацию (--normalizer)'
            )
            return []
    classified = await classify_all(products)
    save_normalized(
        classified, filename_prefix='classified', category=category
    )
    return classified


async def stage_crm(
    products: list | None = None, category: str = ''
) -> list:
    """Этап 4 — задачи в CRM. Без аргумента берёт последний classified.

    Через Redis запоминает id товаров с уже созданными задачами, чтобы
    каждый запуск брал следующие позиции по приоритету (Премиум →
    Стандарт → Эконом), а не дублировал предыдущие.
    """
    if products is None:
        products = load_latest_products('classified', category)
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


def _clear_output_dir() -> int:
    """Удаляет все файлы из output/. Возвращает количество удалённых."""
    out = Path(settings.output_dir)
    removed = 0
    if out.exists():
        for item in out.iterdir():
            if item.is_file():
                item.unlink()
                removed += 1
    return removed


async def stage_reset() -> None:
    """Сброс: удаляет все файлы из output/ и очищает кэш Redis.

    Удобно перед чистым прогоном с нуля.
    """
    removed = _clear_output_dir()
    logger.info('output очищен: удалено {} файлов', removed)

    redis = await make_redis()
    if redis is not None:
        await flush_cache(redis)
    else:
        logger.warning('Redis недоступен — кэш не очищен')


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


async def run_stages(args: argparse.Namespace) -> None:
    """Выполняет выбранные этапы в порядке пайплайна.

    Смежные выбранные этапы передают данные в памяти. Если этап выбран,
    а предыдущий (в цепочке) не выполнялся — данные берутся с диска
    (последний файл соответствующего типа). Порядок флагов в командной
    строке не важен: последовательность задаёт код, а не ввод.
    """
    data: list | None = None   # результат последнего выполненного этапа
    ran_previous = False       # выполнялся ли непосредственно предыдущий
    category = args.category

    if args.parsing:
        data = await stage_parse(args.fresh)
        if not data:
            return
        ran_previous = True
    else:
        ran_previous = False

    if args.normalizer:
        data = stage_normalize(
            data if ran_previous else None, category=category
        )
        if not data:
            return
        ran_previous = True
    else:
        ran_previous = False

    if args.llm:
        data = await stage_llm(
            data if ran_previous else None, category=category
        )
        if not data:
            return
        ran_previous = True
    else:
        ran_previous = False

    if args.crm:
        await stage_crm(
            data if ran_previous else None, category=category
        )


async def _dispatch(args: argparse.Namespace) -> None:
    """Маршрутизация: reset, набор этапов или полный пайплайн."""
    setup_logging()

    if args.reset:
        await stage_reset()

    stages_selected = (
        args.parsing or args.normalizer or args.llm or args.crm
    )

    if stages_selected:
        if args.fresh and not args.parsing:
            logger.warning(
                '--fresh указан, но парсинг не в наборе — '
                'флаг проигнорирован'
            )
        await run_stages(args)
    elif not args.reset:
        # Ни этапов, ни reset — запускаем весь пайплайн
        await run_full(args.fresh)


def _parse_args() -> argparse.Namespace:
    """Разбирает аргументы CLI.

    Этапные флаги комбинируются (--parsing --normalizer --llm).
    Порядок не важен. --reset деструктивный, выполняется первым.
    """
    parser = argparse.ArgumentParser(
        description='Пайплайн аналитики маркетплейсов'
    )
    parser.add_argument(
        '--fresh',
        action='store_true',
        help='парсинг с нуля, игнорируя checkpoint (только с --parsing)',
    )
    parser.add_argument(
        '--parsing', action='store_true', help='этап парсинга'
    )
    parser.add_argument(
        '--normalizer', action='store_true', help='этап нормализации'
    )
    parser.add_argument(
        '--llm', action='store_true', help='этап LLM-классификации'
    )
    parser.add_argument(
        '--crm', action='store_true', help='этап задач в CRM'
    )
    parser.add_argument(
        '--reset',
        action='store_true',
        help='очистить output/ и кэш Redis (выполняется до этапов)',
    )
    parser.add_argument(
        '--category',
        default='',
        help='категория для загрузки файла на отдельном этапе '
             '(например --crm --category смартфоны)',
    )
    return parser.parse_args()


if __name__ == '__main__':
    asyncio.run(_dispatch(_parse_args()))
