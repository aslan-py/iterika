"""Тесты парсера Wildberries."""
import httpx
import pytest
import respx

from src.parser.constants import WB_SEARCH_URL
from src.parser.wb import _fetch_page, fetch_products

_PAGE_100 = [{'id': i, 'name': f'Товар {i}'} for i in range(100)]
_PAGE_50 = [{'id': i, 'name': f'Товар {i}'} for i in range(50)]


@pytest.fixture(autouse=True)
def _settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Настраивает лимиты для тестов независимо от .env.

    settings — синглтон, прочитанный при импорте, поэтому setenv на
    него не влияет: патчим атрибуты объекта напрямую (setattr).
    """
    from src.parser import wb
    monkeypatch.setattr(wb.settings, 'demo_mode', True)
    monkeypatch.setattr(wb.settings, 'demo_limit', 150)
    monkeypatch.setattr(wb.settings, 'wb_delay_min', 0.0)
    monkeypatch.setattr(wb.settings, 'wb_delay_max', 0.0)


class TestFetchPage:
    """Тесты низкоуровневой функции _fetch_page."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_returns_products_on_200(self) -> None:
        """При успешном ответе возвращает список товаров."""
        respx.get(WB_SEARCH_URL).mock(
            return_value=httpx.Response(200, json={'products': _PAGE_100})
        )
        async with httpx.AsyncClient() as client:
            products = await _fetch_page(client, page=1)
        assert len(products) == 100

    @pytest.mark.asyncio
    @respx.mock
    async def test_returns_empty_when_no_products_key(self) -> None:
        """Если в ответе нет ключа products — возвращает пустой список."""
        respx.get(WB_SEARCH_URL).mock(
            return_value=httpx.Response(200, json={})
        )
        async with httpx.AsyncClient() as client:
            products = await _fetch_page(client, page=1)
        assert products == []

    @pytest.mark.asyncio
    @respx.mock
    async def test_raises_on_500(self) -> None:
        """HTTP 500 поднимает исключение (не перехватывается tenacity)."""
        respx.get(WB_SEARCH_URL).mock(
            return_value=httpx.Response(500)
        )
        async with httpx.AsyncClient() as client:
            with pytest.raises(httpx.HTTPStatusError):
                await _fetch_page(client, page=1)

    @pytest.mark.asyncio
    @respx.mock
    async def test_retries_on_429_and_succeeds(self) -> None:
        """При 429 tenacity повторяет запрос и возвращает результат."""
        respx.get(WB_SEARCH_URL).mock(
            side_effect=[
                httpx.Response(429),
                httpx.Response(200, json={'products': _PAGE_100}),
            ]
        )
        async with httpx.AsyncClient() as client:
            products = await _fetch_page(client, page=1)
        assert len(products) == 100


class TestFetchProducts:
    """Тесты высокоуровневой функции fetch_products."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_collects_products_across_pages(self) -> None:
        """Парсер собирает товары постранично."""
        respx.get(WB_SEARCH_URL).mock(
            side_effect=[
                httpx.Response(200, json={'products': _PAGE_100}),
                httpx.Response(200, json={'products': _PAGE_50}),
                httpx.Response(200, json={'products': []}),  # конец
            ]
        )
        products = await fetch_products(fresh=True)
        assert len(products) == 150

    @pytest.mark.asyncio
    @respx.mock
    async def test_stops_on_empty_page(self) -> None:
        """При пустой странице сбор останавливается."""
        respx.get(WB_SEARCH_URL).mock(
            side_effect=[
                httpx.Response(200, json={'products': _PAGE_100}),
                httpx.Response(200, json={'products': []}),
            ]
        )
        products = await fetch_products(fresh=True)
        assert len(products) == 100

    @pytest.mark.asyncio
    @respx.mock
    async def test_graceful_on_network_error(self) -> None:
        """При сетевой ошибке возвращает уже собранные товары."""
        respx.get(WB_SEARCH_URL).mock(
            side_effect=[
                httpx.Response(200, json={'products': _PAGE_100}),
                httpx.ConnectError('timeout'),
            ]
        )
        products = await fetch_products(fresh=True)
        assert len(products) == 100

    @pytest.mark.asyncio
    @respx.mock
    async def test_fresh_starts_from_page_one(self) -> None:
        """fresh=True всегда начинает с первой страницы."""
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return httpx.Response(200, json={'products': _PAGE_50})
            return httpx.Response(200, json={'products': []})

        respx.get(WB_SEARCH_URL).mock(side_effect=handler)
        products = await fetch_products(fresh=True)
        assert len(products) == 50

    @pytest.mark.asyncio
    @respx.mock
    async def test_returns_empty_on_immediate_error(self) -> None:
        """Если первая же страница недоступна — возвращает пустой список."""
        respx.get(WB_SEARCH_URL).mock(
            side_effect=httpx.ConnectError('connection refused')
        )
        products = await fetch_products(fresh=True)
        assert products == []
