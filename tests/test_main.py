"""Тесты оркестрации пайплайна (main.py)."""
import argparse
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import main as _main


def _json_files(directory: Path) -> list[Path]:
    """Список JSON-файлов в папке (синхронно)."""
    return list(directory.glob('*.json'))


def _args(**flags: bool) -> argparse.Namespace:
    """Строит Namespace с дефолтными False для всех флагов CLI."""
    defaults = {
        'fresh': False,
        'parsing': False,
        'normalizer': False,
        'llm': False,
        'crm': False,
        'reset': False,
    }
    defaults.update(flags)
    return argparse.Namespace(**defaults)


class TestStageReset:
    """Тесты команды --reset."""

    @pytest.mark.asyncio
    async def test_removes_output_files(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Все файлы из output/ удаляются."""
        (tmp_path / 'wb_raw_x.json').write_text('[]', encoding='utf-8')
        (tmp_path / 'classified_x.json').write_text('[]', encoding='utf-8')
        monkeypatch.setattr(_main.settings, 'output_dir', str(tmp_path))

        with patch('main.make_redis', return_value=None):
            await _main.stage_reset()

        assert _json_files(tmp_path) == []

    @pytest.mark.asyncio
    async def test_flushes_redis_when_available(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """При доступном Redis вызывается очистка кэша."""
        monkeypatch.setattr(_main.settings, 'output_dir', str(tmp_path))
        mock_redis = MagicMock()

        with (
            patch('main.make_redis', return_value=mock_redis),
            patch('main.flush_cache', new=AsyncMock()) as flush,
        ):
            await _main.stage_reset()

        flush.assert_awaited_once_with(mock_redis)

    @pytest.mark.asyncio
    async def test_no_error_when_output_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Отсутствие папки output не роняет reset."""
        missing = tmp_path / 'nope'
        monkeypatch.setattr(_main.settings, 'output_dir', str(missing))

        with patch('main.make_redis', return_value=None):
            await _main.stage_reset()  # не должно бросить


class TestRunStages:
    """Тесты комбинирования этапных флагов."""

    @pytest.mark.asyncio
    async def test_combined_stages_run_in_order(self) -> None:
        """--parsing --normalizer --llm выполняет три этапа, не CRM."""
        calls: list[str] = []

        async def _parse(fresh: bool) -> list:
            calls.append('parse')
            return [{'id': 1}]

        def _normalize(data: object) -> list:
            calls.append('normalize')
            return ['product']

        async def _llm(data: object) -> list:
            calls.append('llm')
            return ['classified']

        async def _crm(data: object) -> list:
            calls.append('crm')
            return []

        with (
            patch('main.stage_parse', new=_parse),
            patch('main.stage_normalize', new=_normalize),
            patch('main.stage_llm', new=_llm),
            patch('main.stage_crm', new=_crm),
        ):
            await _main.run_stages(
                _args(parsing=True, normalizer=True, llm=True)
            )

        assert calls == ['parse', 'normalize', 'llm']

    @pytest.mark.asyncio
    async def test_order_independent(self) -> None:
        """Порядок флагов не влияет — код идёт по пайплайну."""
        calls: list[str] = []

        async def _parse(fresh: bool) -> list:
            calls.append('parse')
            return [{'id': 1}]

        def _normalize(data: object) -> list:
            calls.append('normalize')
            return ['product']

        with (
            patch('main.stage_parse', new=_parse),
            patch('main.stage_normalize', new=_normalize),
        ):
            # флаги «в обратном порядке» — результат тот же
            await _main.run_stages(_args(normalizer=True, parsing=True))

        assert calls == ['parse', 'normalize']

    @pytest.mark.asyncio
    async def test_gap_loads_from_disk(self) -> None:
        """Пропуск среднего этапа → следующий берёт данные с диска (None)."""
        received: dict[str, object] = {}

        async def _parse(fresh: bool) -> list:
            return [{'id': 1}]

        async def _llm(data: object) -> list:
            received['llm_arg'] = data
            return ['classified']

        with (
            patch('main.stage_parse', new=_parse),
            patch('main.stage_llm', new=_llm),
        ):
            # --parsing --llm без --normalizer: разрыв
            await _main.run_stages(_args(parsing=True, llm=True))

        # llm не получил данные из памяти → None (возьмёт с диска)
        assert received['llm_arg'] is None

    @pytest.mark.asyncio
    async def test_fresh_without_parsing_warns(self) -> None:
        """--fresh без --parsing пишет предупреждение и не роняет."""
        async def _llm(data: object) -> list:
            return ['classified']

        with (
            patch('main.stage_llm', new=_llm),
            patch('main.logger.warning') as warn,
        ):
            await _main._dispatch(_args(llm=True, fresh=True))

        warn.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_flags_runs_full_pipeline(self) -> None:
        """Без флагов запускается полный пайплайн."""
        with patch('main.run_full', new=AsyncMock()) as full:
            await _main._dispatch(_args())
        full.assert_awaited_once()
