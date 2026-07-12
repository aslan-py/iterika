"""Тесты именования и загрузки файлов пайплайна."""
import time
from datetime import datetime, timezone
from pathlib import Path

from src.models import Product
from src.storage import (
    build_filename,
    load_latest_products,
    slugify,
)

_TS = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)


def _write_products(path: Path, category: str) -> None:
    """Записывает один товар указанной категории в JSON-файл."""
    product = Product(
        id='1',
        name='Тест',
        price=1000.0,
        currency='RUB',
        url='https://www.wildberries.ru/catalog/1/detail.aspx',
        category=category,
        collected_at=_TS,
        segment='Стандарт',
    )
    path.write_text(
        f'[{product.model_dump_json()}]', encoding='utf-8'
    )


class TestSlugify:
    """Тесты нормализации категории в имя файла."""

    def test_spaces_to_underscore(self) -> None:
        """Пробелы заменяются на подчёркивания."""
        assert slugify('смартфон apple') == 'смартфон_apple'

    def test_lowercase(self) -> None:
        """Регистр приводится к нижнему."""
        assert slugify('Кроссовки') == 'кроссовки'

    def test_strips_special_chars(self) -> None:
        """Спецсимволы убираются."""
        assert slugify('телефон!!!') == 'телефон'

    def test_collapses_multiple_underscores(self) -> None:
        """Подряд идущие разделители схлопываются в один."""
        assert slugify('телефон   samsung') == 'телефон_samsung'


class TestBuildFilename:
    """Тесты сборки имени файла."""

    def test_contains_prefix_and_category(self) -> None:
        """Имя содержит префикс и slug категории."""
        name = build_filename('classified', 'смартфоны')
        assert name.startswith('classified_смартфоны_')
        assert name.endswith('.json')


class TestLoadLatestByCategory:
    """Тесты загрузки последнего файла по категории."""

    def test_loads_only_requested_category(self, tmp_path: Path) -> None:
        """При указании категории берётся файл именно этой категории."""
        phones = tmp_path / 'classified_смартфоны_2026-07-12_10-00-00.json'
        shoes = tmp_path / 'classified_кроссовки_2026-07-12_11-00-00.json'
        _write_products(phones, 'смартфоны')
        time.sleep(0.01)
        _write_products(shoes, 'кроссовки')

        result = load_latest_products(
            'classified', 'смартфоны', output_dir=str(tmp_path)
        )
        assert result is not None
        assert result[0].category == 'смартфоны'

    def test_none_category_takes_latest_by_time(
        self, tmp_path: Path
    ) -> None:
        """Без категории берётся самый свежий файл по времени создания."""
        phones = tmp_path / 'classified_смартфоны_2026-07-12_10-00-00.json'
        shoes = tmp_path / 'classified_кроссовки_2026-07-12_11-00-00.json'
        _write_products(phones, 'смартфоны')
        time.sleep(0.01)
        _write_products(shoes, 'кроссовки')  # создан позже

        result = load_latest_products(
            'classified', output_dir=str(tmp_path)
        )
        assert result is not None
        assert result[0].category == 'кроссовки'

    def test_missing_returns_none(self, tmp_path: Path) -> None:
        """Нет файла категории → None."""
        result = load_latest_products(
            'classified', 'удочки', output_dir=str(tmp_path)
        )
        assert result is None
