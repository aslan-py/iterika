import sys
from pathlib import Path

from loguru import logger

LOG_DIR = 'logs'

FMT_CONSOLE = (
    '<green>{time:YYYY-MM-DD HH:mm:ss}</green>'
    ' [<level>{level:<8}</level>]'
    ' <cyan>{name}</cyan>:<cyan>{function}</cyan>'
    ' - <level>{message}</level>'
)

FMT_FILE = (
    '{time:YYYY-MM-DD HH:mm:ss}'
    ' [{level:<8}]'
    ' {name}:{function} - {message}'
)

FILE_CONFIG: dict = {
    'format': FMT_FILE,
    'level': 'DEBUG',
    'rotation': '10 MB',
    'retention': 5,
    'encoding': 'utf-8',
}

# Каждый лог-файл собирает записи из перечисленных префиксов имён.
# Redis-клиент (src.redis_client) — часть LLM-этапа (кэш сегментов),
# поэтому его логи направляются в llm.log.
MODULE_PREFIXES: dict[str, tuple[str, ...]] = {
    'parser': ('src.parser',),
    'normalizer': ('src.normalizer', 'src.storage'),
    'llm': ('src.llm', 'src.redis_client'),
    'crm': ('src.crm',),
}


def setup_logging(log_dir: str = LOG_DIR) -> None:
    """Настраивает логирование: консоль + отдельный файл на каждый модуль.

    Создаёт в папке log_dir (по умолчанию logs/) следующие файлы:
      total.log       — все сообщения приложения (DEBUG+)
      parser.log      — только src.parser.*
      normalizer.log  — только src.normalizer.*
      llm.log         — только src.llm.*
      crm.log         — только src.crm.*

    Консоль получает сообщения уровня INFO и выше (с цветом).
    Повторный вызов безопасен: logger.remove() сбрасывает обработчики.
    """
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    logger.remove()

    # Консоль
    logger.add(sys.stderr, format=FMT_CONSOLE, level='INFO')

    # Общий файл
    logger.add(log_path / 'total.log', **FILE_CONFIG)

    # Отдельный файл на каждый модуль
    for module, prefixes in MODULE_PREFIXES.items():
        logger.add(
            log_path / f'{module}.log',
            **FILE_CONFIG,
            filter=lambda r, p=prefixes: (
                (r['name'] or '').startswith(p)
            ),
        )

    logger.info(
        'Система логирования инициализирована: {}',
        log_path.absolute(),
    )
