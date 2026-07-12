"""Итоговый отчёт о прогоне пайплайна.

Собирает статистику по всем этапам (парсинг → нормализация →
классификация → CRM) и сохраняет в output/report_*.json + сводку в лог.
Формируется только для полного прогона, где доступны данные всех этапов.
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

from src.config import settings
from src.models import Product
from src.storage import build_filename


@dataclass
class PipelineReport:
    """Сводная статистика одного прогона пайплайна."""

    category: str
    raw_collected: int          # собрано парсером (сырых записей)
    normalized: int             # уникальных после нормализации
    skipped: int                # отсеяно (дубли + битые)
    segments: dict[str, int]    # распределение по сегментам
    tasks_created: int          # задач создано в CRM
    started_at: str
    finished_at: str
    duration_sec: float


def build_report(
    raw: list[dict],
    products: list[Product],
    classified: list[Product],
    tasks_created: int,
    started_at: datetime,
) -> PipelineReport:
    """Собирает отчёт из данных, прошедших через этапы пайплайна."""
    finished = datetime.now(timezone.utc)
    category = (
        classified[0].category if classified else settings.wb_query
    )
    segments = dict(Counter(p.segment for p in classified if p.segment))
    return PipelineReport(
        category=category,
        raw_collected=len(raw),
        normalized=len(products),
        skipped=len(raw) - len(products),
        segments=segments,
        tasks_created=tasks_created,
        started_at=started_at.isoformat(),
        finished_at=finished.isoformat(),
        duration_sec=round((finished - started_at).total_seconds(), 1),
    )


def save_report(report: PipelineReport, output_dir: str = '') -> Path:
    """Сохраняет отчёт в output/report_<категория>_<время>.json."""
    out = Path(output_dir or settings.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / build_filename('report', report.category)
    path.write_text(
        json.dumps(asdict(report), ensure_ascii=False, indent=2),
        encoding='utf-8',
    )
    logger.info('Отчёт сохранён → {}', path)
    return path


def log_summary(report: PipelineReport) -> None:
    """Выводит читаемую сводку отчёта в лог."""
    segments = ' · '.join(
        f'{name} {count}' for name, count in report.segments.items()
    ) or '—'
    logger.info(
        '\n━━ ОТЧЁТ ━━\n'
        'Категория:   {}\n'
        'Собрано:     {} → {} уникальных ({} отсеяно)\n'
        'Сегменты:    {}\n'
        'Задач в CRM: {}\n'
        'Время:       {} сек',
        report.category,
        report.raw_collected,
        report.normalized,
        report.skipped,
        segments,
        report.tasks_created,
        report.duration_sec,
    )
