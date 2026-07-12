"""Тесты сборки и сохранения отчёта о прогоне."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.models import Product
from src.report import build_report, save_report

_START = datetime(2026, 7, 12, 12, 0, 0, tzinfo=timezone.utc)


def _make_product(pid: str, segment: str) -> Product:
    """Классифицированный товар для отчёта."""
    return Product(
        id=pid,
        name=f'Товар {pid}',
        price=1000.0,
        currency='RUB',
        url=f'https://www.wildberries.ru/catalog/{pid}/detail.aspx',
        category='смартфоны',
        collected_at=_START,
        segment=segment,
    )


class TestBuildReport:
    """Тесты сборки отчёта из данных этапов."""

    def test_counts_raw_and_normalized(self) -> None:
        """Собрано и нормализовано считаются корректно."""
        raw = [{'id': i} for i in range(10)]
        products = [_make_product(str(i), 'Эконом') for i in range(4)]
        report = build_report(raw, products, products, 2, _START)
        assert report.raw_collected == 10
        assert report.normalized == 4
        assert report.skipped == 6

    def test_segment_distribution(self) -> None:
        """Распределение по сегментам подсчитано верно."""
        classified = [
            _make_product('1', 'Эконом'),
            _make_product('2', 'Эконом'),
            _make_product('3', 'Премиум'),
        ]
        report = build_report([{}], classified, classified, 1, _START)
        assert report.segments == {'Эконом': 2, 'Премиум': 1}

    def test_category_from_products(self) -> None:
        """Категория берётся из классифицированных товаров."""
        classified = [_make_product('1', 'Стандарт')]
        report = build_report([{}], classified, classified, 0, _START)
        assert report.category == 'смартфоны'

    def test_tasks_created_recorded(self) -> None:
        """Число задач CRM попадает в отчёт."""
        report = build_report([{}], [], [], 2, _START)
        assert report.tasks_created == 2

    def test_duration_computed(self) -> None:
        """Длительность неотрицательна."""
        report = build_report([{}], [], [], 0, _START)
        assert report.duration_sec >= 0

    def test_empty_classified_uses_settings_category(self) -> None:
        """При пустом classified категория берётся из настроек."""
        report = build_report([{}], [], [], 0, _START)
        assert isinstance(report.category, str)
        assert report.segments == {}


class TestSaveReport:
    """Тесты сохранения отчёта в файл."""

    def test_writes_json_file(self, tmp_path: Path) -> None:
        """Отчёт сохраняется в report_*.json с корректным содержимым."""
        classified = [_make_product('1', 'Премиум')]
        report = build_report([{}, {}], classified, classified, 1, _START)
        path = save_report(report, output_dir=str(tmp_path))

        assert path.exists()
        assert path.name.startswith('report_смартфоны_')
        data = json.loads(path.read_text(encoding='utf-8'))
        assert data['raw_collected'] == 2
        assert data['tasks_created'] == 1
        assert data['segments'] == {'Премиум': 1}

    def test_start_before_finish(self) -> None:
        """finished_at не раньше started_at."""
        late = _START + timedelta(seconds=5)
        report = build_report([{}], [], [], 0, late)
        assert report.finished_at >= report.started_at
