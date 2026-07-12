import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from src.config import settings
from src.models import Product
from src.storage import build_filename

_WB_PRODUCT_URL = 'https://www.wildberries.ru/catalog/{id}/detail.aspx'


def _extract_price(raw: dict[str, Any]) -> float:
    """Извлекает цену в рублях из поля sizes[0].price.product."""
    try:
        return raw['sizes'][0]['price']['product'] / 100
    except (KeyError, IndexError, TypeError, ZeroDivisionError):
        return 0.0


def normalize_one(
    raw: dict[str, Any],
    collected_at: datetime | None = None,
) -> Product | None:
    """Конвертирует один сырой товар WB в объект Product.

    Возвращает None если товар невалиден (нет id или нулевая цена).
    """
    product_id = raw.get('id')
    if not product_id:
        return None

    price = _extract_price(raw)

    name = raw.get('name', '').strip()
    if not name:
        return None

    return Product(
        id=str(product_id),
        name=name,
        price=price,
        currency='RUB',
        url=_WB_PRODUCT_URL.format(id=product_id),
        category=settings.wb_query,
        description=raw.get('brand', ''),
        collected_at=collected_at or datetime.now(timezone.utc),
        segment=None,
    )


def normalize_many(
    raw_products: list[dict[str, Any]],
    collected_at: datetime | None = None,
) -> list[Product]:
    """Нормализует список сырых товаров WB.

    Пропускает невалидные записи и дубли по id. Все товары одного
    прогона получают одну метку времени collected_at.
    """
    ts = collected_at or datetime.now(timezone.utc)
    result: list[Product] = []
    seen_ids: set[str] = set()
    skipped = 0
    duplicates = 0

    for raw in raw_products:
        product = normalize_one(raw, collected_at=ts)
        if product is None:
            skipped += 1
            continue
        if product.id in seen_ids:
            duplicates += 1
            continue
        seen_ids.add(product.id)
        result.append(product)

    logger.info(
        'Нормализация: {} → {} уникальных, {} невалидных, {} дублей',
        len(raw_products),
        len(result),
        skipped,
        duplicates,
    )
    return result


def save_normalized(
    products: list[Product],
    output_dir: str = '',
    filename_prefix: str = 'normalized',
    category: str = '',
) -> Path:
    """Сохраняет товары в JSON. Имя включает категорию товаров."""
    out = Path(output_dir or settings.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    cat = category or (products[0].category if products else settings.wb_query)
    path = out / build_filename(filename_prefix, cat)
    path.write_text(
        json.dumps(
            [p.model_dump(mode='json') for p in products],
            ensure_ascii=False,
            indent=2,
        ),
        encoding='utf-8',
    )
    logger.info('Нормализованные товары сохранены → {}', path)
    return path
