"""КОНТРАКТ между модулями. Меняем только по договорённости всей команды.

Поток данных:
    RecommendRequest -> recommend() -> RecommendResponse
    Внутри: Vendor (каталог) -> Candidate (прошёл фильтры, со score и фактами) -> Card (в ответе)
"""
from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field

# Допустимые значения из каталога (русские, как в CSV)
CITIES = ["Алматы", "Астана", "Зарубежье"]
EVENT_FORMATS = ["свадьба", "той", "корпоратив", "конференция", "юбилей", "день рождения"]
LANGUAGES = ["русский", "казахский", "английский"]
CATEGORIES = [
    "Банкетный зал", "Ведущий", "Ведущий церемонии", "Видеограф", "Декоратор",
    "Загородная площадка", "Инструменталист", "Лайв-бэнд", "Национальный ансамбль",
    "Отель", "Подарки и сувениры", "Ресторан", "Танцевальный коллектив", "Флорист",
    "Фото и видеобудки", "Фотограф", "Шоу-программа",
]
CALENDAR_START = date(2026, 9, 23)
CALENDAR_END = date(2026, 12, 31)


class Vendor(BaseModel):
    """Нормализованный профиль подрядчика (одна строка CSV)."""
    id: str
    name: str
    categories: list[str]
    city: str
    city_imputed: bool
    synthetic: bool
    price_from_kzt: int
    price_imputed: bool
    event_formats: list[str]
    languages: list[str]
    max_hours: int | None  # None — работа не привязана к присутствию (флорист, декор, сувениры)
    busy_dates: frozenset[date]
    description: str


class RecommendRequest(BaseModel):
    city: str
    date: date
    event_format: str
    category: str
    budget_kzt: int = Field(gt=0)
    duration_hours: int | None = Field(default=None, gt=0)
    language: str | None = None


class Status(str, Enum):
    MATCHED = "matched"                        # есть хотя бы одна карточка
    CATEGORY_NOT_FOUND = "category_not_found"  # в городе нет ни одного профиля этой категории
    NO_MATCH = "no_match"                      # профили есть, но никто не прошёл условия


class RejectReason(str, Enum):
    BUSY = "busy"          # занят на дату
    FORMAT = "format"      # не берёт этот формат
    BUDGET = "budget"      # цена «от» выше бюджета
    LANGUAGE = "language"  # не работает на запрошенном языке


class Rejection(BaseModel):
    """Один отсеянный профиль и все причины, по которым он не прошёл."""
    vendor_id: str
    vendor_name: str
    reasons: list[RejectReason]
    detail: str  # человекочитаемо: «цена от 1 000 000 ₸ при бюджете 800 000 ₸»


class Candidate(BaseModel):
    """Внутренний тип: профиль, прошедший фильтры."""
    vendor: Vendor
    score: float
    score_parts: dict[str, float]  # для отладки и панели жюри
    facts: list[str] = []          # проверяемые факты для объяснения (заполняет explain.facts)


class Card(BaseModel):
    id: str
    name: str
    categories: list[str]
    city: str
    price_from_kzt: int
    price_imputed: bool
    city_imputed: bool
    synthetic: bool
    score: float
    score_parts: dict[str, float]
    explanation: str
    explanation_source: Literal["llm", "template"]
    facts: list[str]


class RecommendResponse(BaseModel):
    status: Status
    message: str                  # главное сообщение пользователю, всегда непустое
    shortage_reason: str | None   # почему карточек меньше трёх (если меньше)
    pool_size: int                # сколько профилей этой категории в городе
    passed: int                   # сколько прошли все фильтры
    results: list[Card]
    rejections: list[Rejection]
    elapsed_ms: int = 0
