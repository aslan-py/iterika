"""Тесты нормализатора WB → Product."""
from datetime import datetime, timezone

import pytest

from src.normalizer.normalize import normalize_many, normalize_one

_TS = datetime(2026, 7, 12, 12, 0, 0, tzinfo=timezone.utc)

_RAW_VALID: dict = {
    'id': 12345678,
    'name': 'Кроссовки Nike Air Force White',
    'brand': 'Nike',
    'sizes': [
        {'price': {'product': 299900}},  # 2999.00 руб
    ],
}


class TestNormalizeOne:
    """Тесты функции normalize_one."""

    def test_valid_product_fields(self) -> None:
        """Все поля корректно маппятся из сырого WB JSON."""
        product = normalize_one(_RAW_VALID, collected_at=_TS)
        assert product is not None
        assert product.id == '12345678'
        assert product.name == 'Кроссовки Nike Air Force White'
        assert product.price == 2999.0
        assert product.currency == 'RUB'
        assert product.url == 'https://www.wildberries.ru/catalog/12345678/detail.aspx'
        assert product.description == 'Nike'
        assert product.segment is None
        assert product.collected_at == _TS

    def test_url_uses_product_id(self) -> None:
        """URL конструируется из id по шаблону WB."""
        raw = {**_RAW_VALID, 'id': 99999999}
        product = normalize_one(raw)
        assert product is not None
        assert '99999999' in product.url

    def test_price_extracted_from_sizes(self) -> None:
        """Цена берётся из sizes[0].price.product / 100."""
        raw = {**_RAW_VALID, 'sizes': [{'price': {'product': 150000}}]}
        product = normalize_one(raw)
        assert product is not None
        assert product.price == 1500.0

    def test_price_zero_when_no_sizes(self) -> None:
        """При отсутствии sizes цена равна 0.0, товар не отбраковывается."""
        raw = {**_RAW_VALID, 'sizes': []}
        product = normalize_one(raw)
        assert product is not None
        assert product.price == 0.0

    def test_price_zero_when_sizes_missing(self) -> None:
        """При отсутствии ключа sizes цена равна 0.0."""
        raw = {k: v for k, v in _RAW_VALID.items() if k != 'sizes'}
        product = normalize_one(raw)
        assert product is not None
        assert product.price == 0.0

    def test_returns_none_when_no_id(self) -> None:
        """Товар без id отбраковывается."""
        assert normalize_one({'name': 'Test', 'sizes': []}) is None

    def test_returns_none_when_id_is_none(self) -> None:
        """Товар с id=None отбраковывается."""
        raw = {**_RAW_VALID, 'id': None}
        assert normalize_one(raw) is None

    def test_returns_none_when_name_empty(self) -> None:
        """Товар с пустым name отбраковывается."""
        raw = {**_RAW_VALID, 'name': ''}
        assert normalize_one(raw) is None

    def test_returns_none_when_name_whitespace(self) -> None:
        """Товар с name из пробелов отбраковывается."""
        raw = {**_RAW_VALID, 'name': '   '}
        assert normalize_one(raw) is None

    def test_description_falls_back_to_empty(self) -> None:
        """Если brand отсутствует — description пустая строка."""
        raw = {k: v for k, v in _RAW_VALID.items() if k != 'brand'}
        product = normalize_one(raw)
        assert product is not None
        assert product.description == ''

    def test_collected_at_uses_provided_timestamp(self) -> None:
        """collected_at берётся из переданного аргумента."""
        product = normalize_one(_RAW_VALID, collected_at=_TS)
        assert product is not None
        assert product.collected_at == _TS

    def test_collected_at_auto_set_when_none(self) -> None:
        """Если collected_at не передан — проставляется текущее UTC-время."""
        product = normalize_one(_RAW_VALID)
        assert product is not None
        assert product.collected_at.tzinfo is not None


class TestNormalizeMany:
    """Тесты функции normalize_many."""

    def test_all_valid_returns_all(self) -> None:
        """Все валидные товары возвращаются."""
        raw_list = [
            _RAW_VALID,
            {**_RAW_VALID, 'id': 99999, 'name': 'Второй товар'},
        ]
        products = normalize_many(raw_list)
        assert len(products) == 2

    def test_skips_invalid_keeps_valid(self) -> None:
        """Невалидные пропускаются, валидные остаются."""
        raw_list = [
            _RAW_VALID,
            {'id': None, 'name': 'Без id'},
            {'id': 11111, 'name': ''},
            {**_RAW_VALID, 'id': 22222, 'name': 'Валидный второй'},
        ]
        products = normalize_many(raw_list)
        assert len(products) == 2

    def test_empty_input_returns_empty(self) -> None:
        """Пустой список на входе → пустой список на выходе."""
        assert normalize_many([]) == []

    def test_all_invalid_returns_empty(self) -> None:
        """Если все товары невалидны — возвращается пустой список."""
        raw_list = [
            {'id': None, 'name': 'x'},
            {'id': 1, 'name': ''},
        ]
        assert normalize_many(raw_list) == []

    def test_shared_timestamp_across_batch(self) -> None:
        """Все товары одного прогона получают одинаковый collected_at."""
        raw_list = [
            _RAW_VALID,
            {**_RAW_VALID, 'id': 99999, 'name': 'Второй'},
        ]
        products = normalize_many(raw_list, collected_at=_TS)
        assert all(p.collected_at == _TS for p in products)

    def test_returns_product_instances(self) -> None:
        """Все элементы результата — объекты Product."""
        from src.models import Product
        products = normalize_many([_RAW_VALID])
        assert all(isinstance(p, Product) for p in products)

    def test_deduplication_keeps_first_occurrence(self) -> None:
        """Дублирующийся id встречается в результате ровно один раз."""
        raw_list = [_RAW_VALID, _RAW_VALID, _RAW_VALID]
        products = normalize_many(raw_list)
        assert len(products) == 1

    def test_deduplication_preserves_unique_ids(self) -> None:
        """Товары с разными id не отбрасываются."""
        raw_list = [
            _RAW_VALID,
            {**_RAW_VALID, 'id': 99999, 'name': 'Другой товар'},
        ]
        products = normalize_many(raw_list)
        assert len(products) == 2

    def test_deduplication_mixed_valid_and_duplicate(self) -> None:
        """Из трёх записей (2 дубля + 1 уникальная) остаётся 2."""
        raw_list = [
            _RAW_VALID,
            _RAW_VALID,
            {**_RAW_VALID, 'id': 77777, 'name': 'Уникальный'},
        ]
        products = normalize_many(raw_list)
        ids = [p.id for p in products]
        assert len(products) == 2
        assert ids.count('12345678') == 1
