"""Тесты отбора позиций и интеграции с AmoCRM."""
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import respx

import src.crm.amocrm as _amo
from src.crm.amocrm import _build_task, create_tasks
from src.crm.selector import select_interesting
from src.models import Product, Segment
from src.redis_client import get_created_task_ids, mark_tasks_created

_TS = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)


def _count_task_files(directory: str) -> int:
    """Считает созданные fallback-файлы задач (синхронно)."""
    return len(list(Path(directory).glob('tasks_*.json')))


def _make_product(
    pid: str, name: str, price: float, segment: str
) -> Product:
    """Фабрика классифицированного товара."""
    return Product(
        id=pid,
        name=name,
        price=price,
        currency='RUB',
        url=f'https://www.wildberries.ru/catalog/{pid}/detail.aspx',
        category='смартфоны',
        collected_at=_TS,
        segment=segment,
    )


class TestSelectInteresting:
    """Тесты отбора интересных позиций."""

    def test_empty_returns_empty(self) -> None:
        """Пустой список → пустой список."""
        assert select_interesting([]) == []

    def test_returns_limit_count(self) -> None:
        """Возвращает ровно limit позиций."""
        products = [
            _make_product(str(i), f'Товар {i}', float(i * 1000),
                          Segment.PREMIUM.value)
            for i in range(1, 6)
        ]
        assert len(select_interesting(products, limit=2)) == 2

    def test_premium_prioritized_over_economy(self) -> None:
        """Премиум отбирается раньше Эконома."""
        products = [
            _make_product('1', 'Дешёвый', 500.0, Segment.ECONOMY.value),
            _make_product('2', 'Дорогой', 90000.0, Segment.PREMIUM.value),
        ]
        result = select_interesting(products, limit=1)
        assert result[0].segment == Segment.PREMIUM.value

    def test_expensive_first_within_segment(self) -> None:
        """Внутри сегмента дороже — раньше."""
        products = [
            _make_product('1', 'A', 50000.0, Segment.PREMIUM.value),
            _make_product('2', 'B', 90000.0, Segment.PREMIUM.value),
        ]
        result = select_interesting(products, limit=1)
        assert result[0].id == '2'

    def test_deduplicates_by_name(self) -> None:
        """Одинаковые названия не дают двух задач по одной модели."""
        products = [
            _make_product('1', 'iPhone 15', 90000.0, Segment.PREMIUM.value),
            _make_product('2', 'iPhone 15', 88000.0, Segment.PREMIUM.value),
            _make_product('3', 'Galaxy S24', 85000.0, Segment.PREMIUM.value),
        ]
        result = select_interesting(products, limit=2)
        names = {p.name for p in result}
        assert names == {'iPhone 15', 'Galaxy S24'}

    def test_exclude_ids_skips_created(self) -> None:
        """Товары из exclude_ids пропускаются — берутся следующие."""
        products = [
            _make_product('1', 'iPhone 15', 90000.0, Segment.PREMIUM.value),
            _make_product('2', 'Galaxy S24', 85000.0, Segment.PREMIUM.value),
            _make_product('3', 'Pixel 8', 80000.0, Segment.PREMIUM.value),
        ]
        result = select_interesting(products, limit=2, exclude_ids={'1'})
        ids = {p.id for p in result}
        assert '1' not in ids
        assert ids == {'2', '3'}

    def test_priority_order_across_segments(self) -> None:
        """Сначала выбирается весь Премиум, затем Стандарт, затем Эконом."""
        products = [
            _make_product('1', 'Эконом-товар', 500.0, Segment.ECONOMY.value),
            _make_product('2', 'Премиум-товар', 90000.0,
                          Segment.PREMIUM.value),
            _make_product('3', 'Стандарт-товар', 20000.0,
                          Segment.STANDARD.value),
        ]
        # первый вызов → Премиум
        first = select_interesting(products, limit=1)
        assert first[0].segment == Segment.PREMIUM.value
        # исключаем Премиум → следующий Стандарт
        second = select_interesting(products, limit=1, exclude_ids={'2'})
        assert second[0].segment == Segment.STANDARD.value
        # исключаем Премиум и Стандарт → Эконом
        third = select_interesting(
            products, limit=1, exclude_ids={'2', '3'}
        )
        assert third[0].segment == Segment.ECONOMY.value


class TestCrmTracking:
    """Тесты Redis-трекинга созданных задач."""

    @pytest.mark.asyncio
    async def test_get_created_task_ids_returns_set(self) -> None:
        """get_created_task_ids возвращает множество id из Redis."""
        mock_redis = MagicMock()
        mock_redis.smembers = AsyncMock(return_value={'1', '2'})
        ids = await get_created_task_ids(mock_redis)
        assert ids == {'1', '2'}

    @pytest.mark.asyncio
    async def test_mark_tasks_created_calls_sadd(self) -> None:
        """mark_tasks_created добавляет id в Redis-множество."""
        mock_redis = MagicMock()
        mock_redis.sadd = AsyncMock()
        await mark_tasks_created(mock_redis, ['3', '4'])
        mock_redis.sadd.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_mark_tasks_created_empty_noop(self) -> None:
        """Пустой список не обращается к Redis."""
        mock_redis = MagicMock()
        mock_redis.sadd = AsyncMock()
        await mark_tasks_created(mock_redis, [])
        mock_redis.sadd.assert_not_awaited()


class TestBuildTask:
    """Тесты формирования тела задачи AmoCRM."""

    def test_task_has_required_fields(self) -> None:
        """Задача содержит text, complete_till, task_type_id."""
        p = _make_product('1', 'iPhone 15', 90000.0, Segment.PREMIUM.value)
        task = _build_task(p)
        assert 'text' in task
        assert 'complete_till' in task
        assert 'task_type_id' in task

    def test_task_text_contains_product_info(self) -> None:
        """Текст задачи содержит название, сегмент и цену."""
        p = _make_product('1', 'iPhone 15', 90000.0, Segment.PREMIUM.value)
        text = _build_task(p)['text']
        assert 'iPhone 15' in text
        assert 'Премиум' in text
        assert '90000' in text


class TestCreateTasks:
    """Тесты создания задач с fallback."""

    @pytest.fixture(autouse=True)
    def _cfg(self, tmp_path: object, monkeypatch: pytest.MonkeyPatch) -> None:
        """Изолирует output и сбрасывает токен по умолчанию."""
        monkeypatch.setattr(_amo.settings, 'output_dir', str(tmp_path))
        monkeypatch.setattr(_amo.settings, 'amocrm_access_token', '')
        monkeypatch.setattr(_amo.settings, 'amocrm_subdomain', 'test')

    @pytest.mark.asyncio
    async def test_empty_products_returns_empty(self) -> None:
        """Нет позиций → нет задач."""
        assert await create_tasks([]) == []

    @pytest.mark.asyncio
    async def test_fallback_when_no_token(self, tmp_path: object) -> None:
        """Без токена задачи уходят в JSON-файл."""
        products = [
            _make_product('1', 'iPhone 15', 90000.0, Segment.PREMIUM.value)
        ]
        tasks = await create_tasks(products)
        assert len(tasks) == 1
        n_files = _count_task_files(str(tmp_path))
        assert n_files == 1

    @pytest.mark.asyncio
    @respx.mock
    async def test_posts_to_amocrm_with_token(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """С токеном задачи уходят в AmoCRM API."""
        monkeypatch.setattr(_amo.settings, 'amocrm_access_token', 'tok123')
        route = respx.post(
            'https://test.amocrm.ru/api/v4/tasks'
        ).mock(return_value=httpx.Response(200, json={'_embedded': {}}))
        products = [
            _make_product('1', 'iPhone 15', 90000.0, Segment.PREMIUM.value)
        ]
        tasks = await create_tasks(products)
        assert route.called
        assert len(tasks) == 1

    @pytest.mark.asyncio
    @respx.mock
    async def test_fallback_on_api_error(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: object
    ) -> None:
        """При ошибке API задачи сохраняются в JSON."""
        monkeypatch.setattr(_amo.settings, 'amocrm_access_token', 'tok123')
        respx.post('https://test.amocrm.ru/api/v4/tasks').mock(
            return_value=httpx.Response(401)
        )
        products = [
            _make_product('1', 'iPhone 15', 90000.0, Segment.PREMIUM.value)
        ]
        tasks = await create_tasks(products)
        assert len(tasks) == 1
        n_files = _count_task_files(str(tmp_path))
        assert n_files == 1
