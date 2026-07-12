import json
from pathlib import Path

from loguru import logger

from src.config import settings

_DIR = Path(settings.output_dir)
_META = _DIR / 'checkpoint.json'
_PARTIAL = _DIR / 'wb_partial.json'


def save(page: int, query: str, products: list[dict]) -> None:
    """Сохраняет прогресс: метаданные и товары в partial-файл."""
    _DIR.mkdir(parents=True, exist_ok=True)
    _PARTIAL.write_text(
        json.dumps(products, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )
    _META.write_text(
        json.dumps(
            {'page': page, 'query': query, 'collected': len(products)},
            ensure_ascii=False,
        ),
        encoding='utf-8',
    )
    logger.debug(
        'Checkpoint: следующая страница {}, собрано {}',
        page,
        len(products),
    )


def load(query: str) -> tuple[int, list[dict]] | None:
    """Загружает прогресс, если checkpoint есть и запрос совпадает.

    Возвращает (следующая_страница, собранные_товары) или None,
    если нужно начать заново.
    """
    if not _META.exists():
        return None
    try:
        data = json.loads(_META.read_text(encoding='utf-8'))
    except Exception as exc:
        logger.warning(
            'Не удалось прочитать checkpoint: {} — начинаем заново', exc
        )
        return None
    if data.get('query') != query:
        logger.info(
            'Checkpoint для другого запроса ("{}") — начинаем заново',
            data.get('query'),
        )
        return None
    if not _PARTIAL.exists():
        logger.warning('wb_partial.json не найден — начинаем заново')
        return None
    products = json.loads(_PARTIAL.read_text(encoding='utf-8'))
    logger.info(
        'Возобновляем сбор с страницы {}, уже собрано {} товаров',
        data['page'],
        len(products),
    )
    return data['page'], products


def delete() -> None:
    """Удаляет checkpoint и partial-файл после успешного завершения сбора."""
    _META.unlink(missing_ok=True)
    _PARTIAL.unlink(missing_ok=True)
    logger.debug('Checkpoint удалён')
