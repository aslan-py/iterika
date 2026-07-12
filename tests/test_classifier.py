"""Тесты LLM-классификатора ценовых сегментов."""
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import src.llm.classifier as _mod
from src.llm.classifier import (
    PriceStats,
    _build_prompt,
    _compute_stats,
    _fallback_by_price,
    _group_by_name,
    _name_key,
    _representative,
    classify_all,
)
from src.models import Product, Segment

_TS = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)


def _make_product(pid: str, name: str, price: float) -> Product:
    """Фабрика тестового товара."""
    return Product(
        id=pid,
        name=name,
        price=price,
        currency='RUB',
        url=f'https://www.wildberries.ru/catalog/{pid}/detail.aspx',
        category='кроссовки',
        collected_at=_TS,
    )


_STATS = PriceStats(
    min=100.0, p_low=1000.0, median=5000.0, p_high=15000.0, max=25000.0
)


class TestNameKey:
    """Тесты нормализации названия в ключ."""

    def test_lowercases(self) -> None:
        """Регистр не влияет на ключ."""
        assert _name_key('iPhone 13') == _name_key('IPHONE 13')

    def test_collapses_whitespace(self) -> None:
        """Множественные пробелы схлопываются в один."""
        assert _name_key('iPhone  13   Pro') == 'iphone 13 pro'

    def test_strips_edges(self) -> None:
        """Пробелы по краям убираются."""
        assert _name_key('  Nike Air  ') == 'nike air'


class TestComputeStats:
    """Тесты вычисления ценовой статистики."""

    def test_min_max_median(self) -> None:
        """min, max и median корректно вычисляются."""
        products = [
            _make_product('1', 'A', 100.0),
            _make_product('2', 'B', 5000.0),
            _make_product('3', 'C', 25000.0),
        ]
        stats = _compute_stats(products)
        assert stats.min == 100.0
        assert stats.max == 25000.0
        assert stats.median == 5000.0

    def test_p_low_less_than_p_high(self) -> None:
        """p_low (граница Эконом/Стандарт) меньше p_high (Стандарт/Премиум)."""
        products = [
            _make_product(str(i), f'T{i}', float(i * 1000))
            for i in range(1, 10)
        ]
        stats = _compute_stats(products)
        assert stats.p_low < stats.p_high

    def test_single_product(self) -> None:
        """Один товар: все поля равны одному значению."""
        products = [_make_product('1', 'A', 3000.0)]
        stats = _compute_stats(products)
        assert (
            stats.min
            == stats.p_low
            == stats.median
            == stats.p_high
            == stats.max
        )


class TestRepresentative:
    """Тесты выбора представителя группы одинаковых товаров."""

    def test_picks_median_price(self) -> None:
        """Представитель — товар с медианной ценой группы."""
        group = [
            _make_product('1', 'iPhone', 10000.0),
            _make_product('2', 'iPhone', 50000.0),
            _make_product('3', 'iPhone', 30000.0),
        ]
        assert _representative(group).price == 30000.0

    def test_single_item_group(self) -> None:
        """Группа из одного товара возвращает его же."""
        group = [_make_product('1', 'iPhone', 42000.0)]
        assert _representative(group).id == '1'


class TestGroupByName:
    """Тесты группировки товаров по названию."""

    def test_same_name_grouped(self) -> None:
        """Товары с одинаковым названием (разный id) — одна группа."""
        products = [
            _make_product('1', 'Galaxy A57', 30000.0),
            _make_product('2', 'Galaxy A57', 31000.0),
            _make_product('3', 'iPhone 13', 40000.0),
        ]
        groups = _group_by_name(products)
        assert len(groups) == 2

    def test_case_insensitive_grouping(self) -> None:
        """Различие в регистре не создаёт новую группу."""
        products = [
            _make_product('1', 'iPhone 13', 40000.0),
            _make_product('2', 'IPHONE 13', 41000.0),
        ]
        assert len(_group_by_name(products)) == 1


class TestFallbackByPrice:
    """Тесты ценового fallback-классификатора."""

    def test_empty_returns_empty(self) -> None:
        """Пустой список → пустой список сегментов."""
        assert _fallback_by_price([], _STATS) == []

    def test_returns_same_count_as_input(self) -> None:
        """Количество сегментов совпадает с количеством товаров."""
        products = [
            _make_product(str(i), f'T{i}', float(i * 1000))
            for i in range(1, 7)
        ]
        stats = _compute_stats(products)
        assert len(_fallback_by_price(products, stats)) == 6

    def test_all_values_are_valid_segments(self) -> None:
        """Все возвращаемые значения — допустимые сегменты."""
        products = [
            _make_product(str(i), f'T{i}', float(i * 1000))
            for i in range(1, 10)
        ]
        stats = _compute_stats(products)
        valid = {s.value for s in Segment}
        for seg in _fallback_by_price(products, stats):
            assert seg in valid

    def test_cheapest_gets_economy(self) -> None:
        """Самый дешёвый товар из пяти получает сегмент Эконом."""
        products = [
            _make_product('1', 'A', 100.0),
            _make_product('2', 'B', 1000.0),
            _make_product('3', 'C', 5000.0),
            _make_product('4', 'D', 15000.0),
            _make_product('5', 'E', 25000.0),
        ]
        stats = _compute_stats(products)
        assert _fallback_by_price(products, stats)[0] == Segment.ECONOMY.value

    def test_most_expensive_gets_premium(self) -> None:
        """Самый дорогой товар из пяти получает сегмент Премиум."""
        products = [
            _make_product('1', 'A', 100.0),
            _make_product('2', 'B', 1000.0),
            _make_product('3', 'C', 5000.0),
            _make_product('4', 'D', 15000.0),
            _make_product('5', 'E', 25000.0),
        ]
        stats = _compute_stats(products)
        assert _fallback_by_price(products, stats)[4] == Segment.PREMIUM.value

    def test_three_products_cover_all_segments(self) -> None:
        """Три товара с резко разными ценами получают все три сегмента."""
        products = [
            _make_product('1', 'A', 100.0),
            _make_product('2', 'B', 5000.0),
            _make_product('3', 'C', 25000.0),
        ]
        stats = _compute_stats(products)
        result = _fallback_by_price(products, stats)
        assert Segment.ECONOMY.value in result
        assert Segment.STANDARD.value in result
        assert Segment.PREMIUM.value in result


class TestBuildPrompt:
    """Тесты генерации промпта для LLM."""

    def test_prompt_contains_product_name(self) -> None:
        """Промпт содержит название товара."""
        p = _make_product('1', 'Nike Air Force', 5000.0)
        assert 'Nike Air Force' in _build_prompt([p], _STATS)

    def test_prompt_contains_category(self) -> None:
        """Промпт содержит категорию товаров."""
        p = _make_product('1', 'Тест', 1000.0)
        assert 'кроссовки' in _build_prompt([p], _STATS)

    def test_prompt_contains_price_boundaries(self) -> None:
        """Промпт содержит конкретные ценовые границы из stats."""
        p = _make_product('1', 'T', 1000.0)
        prompt = _build_prompt([p], _STATS)
        assert '1000' in prompt   # p_low
        assert '15000' in prompt  # p_high

    def test_prompt_mentions_flagship_rule(self) -> None:
        """Промпт содержит правило подъёма флагманов."""
        p = _make_product('1', 'iPhone', 1000.0)
        assert 'ФЛАГМАН' in _build_prompt([p], _STATS)

    def test_prompt_contains_expected_count(self) -> None:
        """Промпт содержит ожидаемое количество ответов."""
        products = [_make_product(str(i), f'T{i}', 1000.0) for i in range(5)]
        assert '5' in _build_prompt(products, _STATS)

    def test_prompt_mentions_all_segments(self) -> None:
        """Промпт описывает все три сегмента."""
        p = _make_product('1', 'Test', 1000.0)
        prompt = _build_prompt([p], _STATS)
        assert 'Эконом' in prompt
        assert 'Стандарт' in prompt
        assert 'Премиум' in prompt


class TestClassifyAll:
    """Тесты основной функции классификации."""

    @pytest.mark.asyncio
    async def test_empty_list_returns_empty(self) -> None:
        """Пустой список → пустой список."""
        assert await classify_all([]) == []

    @pytest.mark.asyncio
    async def test_fallback_when_no_client_no_redis(self) -> None:
        """Без API-ключа и без Redis все товары классифицируются по цене."""
        products = [
            _make_product('1', 'A', 100.0),
            _make_product('2', 'B', 5000.0),
            _make_product('3', 'C', 25000.0),
        ]
        with (
            patch('src.llm.classifier.make_redis', return_value=None),
            patch('src.llm.classifier._make_client', return_value=None),
        ):
            result = await classify_all(products)
        assert len(result) == 3
        assert all(p.segment is not None for p in result)

    @pytest.mark.asyncio
    async def test_llm_segments_applied_in_order(self) -> None:
        """Сегменты от LLM проставляются в правильном порядке."""
        products = [
            _make_product('1', 'A', 100.0),
            _make_product('2', 'B', 5000.0),
            _make_product('3', 'C', 25000.0),
        ]
        expected = [
            Segment.ECONOMY.value,
            Segment.STANDARD.value,
            Segment.PREMIUM.value,
        ]

        async def _stub(
            client: Any, chunk: list[Product], stats: Any
        ) -> list[str]:
            return expected

        with (
            patch('src.llm.classifier.make_redis', return_value=None),
            patch(
                'src.llm.classifier._make_client',
                return_value=AsyncMock(),
            ),
            patch('src.llm.classifier._classify_batch', new=_stub),
        ):
            result = await classify_all(products)

        assert [p.segment for p in result] == expected

    @pytest.mark.asyncio
    async def test_duplicate_names_classified_once(self) -> None:
        """Одинаковые названия идут в LLM одним представителем."""
        products = [
            _make_product('1', 'Galaxy A57', 30000.0),
            _make_product('2', 'Galaxy A57', 31000.0),
            _make_product('3', 'Galaxy A57', 32000.0),
            _make_product('4', 'iPhone 15', 80000.0),
        ]
        seen_sizes: list[int] = []

        async def _stub(
            client: Any, chunk: list[Product], stats: Any
        ) -> list[str]:
            seen_sizes.append(len(chunk))
            return [Segment.STANDARD.value] * len(chunk)

        with (
            patch('src.llm.classifier.make_redis', return_value=None),
            patch(
                'src.llm.classifier._make_client',
                return_value=AsyncMock(),
            ),
            patch('src.llm.classifier._classify_batch', new=_stub),
        ):
            result = await classify_all(products)

        # В LLM ушло 2 уникальных названия, не 4 товара
        assert seen_sizes == [2]
        # Но результат покрывает все 4 товара
        assert len(result) == 4
        assert all(p.segment is not None for p in result)

    @pytest.mark.asyncio
    async def test_same_name_gets_same_segment(self) -> None:
        """Все товары с одинаковым названием получают один сегмент."""
        products = [
            _make_product('1', 'iPhone 13', 15000.0),
            _make_product('2', 'iPhone 13', 45000.0),
        ]

        async def _stub(
            client: Any, chunk: list[Product], stats: Any
        ) -> list[str]:
            return [Segment.PREMIUM.value] * len(chunk)

        with (
            patch('src.llm.classifier.make_redis', return_value=None),
            patch(
                'src.llm.classifier._make_client',
                return_value=AsyncMock(),
            ),
            patch('src.llm.classifier._classify_batch', new=_stub),
        ):
            result = await classify_all(products)

        assert result[0].segment == result[1].segment == Segment.PREMIUM.value

    @pytest.mark.asyncio
    async def test_product_fields_preserved(self) -> None:
        """Id и name сохраняются после классификации."""
        products = [_make_product('42', 'Кроссовки Nike', 4999.0)]
        with (
            patch('src.llm.classifier.make_redis', return_value=None),
            patch('src.llm.classifier._make_client', return_value=None),
        ):
            result = await classify_all(products)
        assert result[0].id == '42'
        assert result[0].name == 'Кроссовки Nike'

    @pytest.mark.asyncio
    async def test_cached_names_skip_llm(self) -> None:
        """Названия из кэша не отправляются в LLM."""
        products = [
            _make_product('1', 'A', 100.0),
            _make_product('2', 'B', 5000.0),
        ]
        batch_called = False

        async def _stub(
            client: Any, chunk: list[Product], stats: Any
        ) -> list[str]:
            nonlocal batch_called
            batch_called = True
            return [Segment.STANDARD.value] * len(chunk)

        mock_redis = MagicMock()
        mock_redis.get = AsyncMock(return_value=Segment.PREMIUM.value)
        mock_redis.ping = AsyncMock(return_value=True)
        mock_redis.pipeline = MagicMock(return_value=MagicMock())

        with (
            patch('src.llm.classifier.make_redis', return_value=mock_redis),
            patch('src.llm.classifier.get_segment', return_value='Премиум'),
            patch('src.llm.classifier._classify_batch', new=_stub),
        ):
            result = await classify_all(products)

        assert not batch_called
        assert all(p.segment == 'Премиум' for p in result)

    @pytest.mark.asyncio
    async def test_batching_splits_products_correctly(self) -> None:
        """Уникальные названия разбиваются на батчи (batch_size=2)."""
        products = [_make_product(str(i), f'T{i}', 1000.0) for i in range(5)]
        batch_sizes: list[int] = []

        async def _stub(
            client: Any, chunk: list[Product], stats: Any
        ) -> list[str]:
            batch_sizes.append(len(chunk))
            return [Segment.STANDARD.value] * len(chunk)

        mock_settings = type(
            'MockSettings',
            (),
            {
                'deepseek_api_key': 'sk-test',
                'deepseek_base_url': 'https://api.deepseek.com',
                'deepseek_model': 'deepseek-chat',
                'llm_batch_size': 2,
                'segment_p_economy': 0.30,
                'segment_p_premium': 0.80,
            },
        )()

        with (
            patch.object(_mod, 'settings', mock_settings),
            patch('src.llm.classifier.make_redis', return_value=None),
            patch(
                'src.llm.classifier._make_client',
                return_value=AsyncMock(),
            ),
            patch('src.llm.classifier._classify_batch', new=_stub),
        ):
            result = await classify_all(products)

        assert len(result) == 5
        assert batch_sizes == [2, 2, 1]
