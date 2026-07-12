from datetime import datetime
from enum import Enum

from pydantic import BaseModel


class Segment(str, Enum):
    """Ценовой сегмент товара, определяемый классификатором на базе LLM."""

    ECONOMY = 'Эконом'
    STANDARD = 'Стандарт'
    PREMIUM = 'Премиум'


class Product(BaseModel):
    """Модель нормализованного товара с маркетплейса.

    Используется для валидации данных, полученных в результате парсинга
    или через внешние API.
    """

    id: str
    name: str
    price: float
    currency: str = 'RUB'
    url: str
    category: str
    description: str = ''
    collected_at: datetime
    segment: str | None = None
