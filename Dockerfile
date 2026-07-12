FROM python:3.12-slim

WORKDIR /app

RUN pip install uv

# Ставим только прод-зависимости (без dev: pytest, ruff)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY . .

# Запуск всего пайплайна: парсинг → нормализация → LLM → CRM → отчёт
CMD ["uv", "run", "--no-dev", "python", "main.py"]
