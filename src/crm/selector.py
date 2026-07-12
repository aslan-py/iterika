"""Отбор «интересных позиций» для постановки задач менеджерам."""
from __future__ import annotations

from loguru import logger

from src.models import Product, Segment

# Приоритет сегментов при отборе: Премиум интереснее для проработки
# (маржинальные нишевые позиции), затем Стандарт, затем Эконом.
_SEGMENT_RANK: dict[str, int] = {
    Segment.PREMIUM.value: 0,
    Segment.STANDARD.value: 1,
    Segment.ECONOMY.value: 2,
}


def _rank(product: Product) -> tuple[int, float]:
    """Ключ сортировки: сначала сегмент, внутри — дороже выше."""
    seg_rank = _SEGMENT_RANK.get(product.segment or '', 99)
    return (seg_rank, -product.price)


def select_interesting(
    products: list[Product], limit: int = 2
) -> list[Product]:
    """Отбирает топ-`limit` позиций для задач менеджерам.

    Эвристика: самые дорогие товары приоритетного сегмента (Премиум →
    Стандарт → Эконом). Дедуплицирует по названию, чтобы две задачи
    не касались одной и той же модели.
    """
    if not products:
        return []

    ordered = sorted(products, key=_rank)

    selected: list[Product] = []
    seen_names: set[str] = set()
    for product in ordered:
        key = product.name.strip().lower()
        if key in seen_names:
            continue
        seen_names.add(key)
        selected.append(product)
        if len(selected) >= limit:
            break

    logger.info(
        'Отбор позиций: {} кандидатов → {} задач',
        len(products),
        len(selected),
    )
    return selected
