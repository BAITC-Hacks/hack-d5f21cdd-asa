"""[Участник 1] Ранжирование прошедших фильтры.

Score — сумма прозрачных компонент (score_parts видны в UI для жюри).
Сортировка: score по убыванию, затем id по возрастанию -> детерминизм.

TODO(У1): подобрать веса на демо-запросах;
TODO(У2): semantic — близость описания к запросу (эмбеддинги/LLM-теги), сейчас — ключевые слова.
"""
from __future__ import annotations

from app.models import Candidate, RecommendRequest, Vendor

# Основы слов формата для поиска в описании
FORMAT_STEMS = {
    "свадьба": ["свадеб", "свадьб", "молодожён", "молодожен", "невест", "бракосочет"],
    "той": ["той", "тоев", "казахск", "национальн"],
    "корпоратив": ["корпоратив", "бизнес", "компани", "бренд"],
    "конференция": ["конференц", "форум", "делов", "презентац"],
    "юбилей": ["юбиле"],
    "день рождения": ["день рождения", "дня рождения", "детск"],
}


def specialization(v: Vendor, req: RecommendRequest) -> float:
    """Формат стоит первым в списке -> подрядчик на нём специализируется."""
    idx = v.event_formats.index(req.event_format)
    return max(0.0, 20.0 - 5.0 * idx)


def budget_fit(v: Vendor, req: RecommendRequest) -> float:
    """Запас по бюджету, но без перекоса в «самый дешёвый»: максимум при цене 50–90% бюджета."""
    ratio = v.price_from_kzt / req.budget_kzt
    if 0.5 <= ratio <= 0.9:
        return 15.0
    if ratio < 0.5:
        return 15.0 * (0.5 + ratio)  # очень дешёвый — ок, но не лучший сигнал
    return 15.0 * (1.0 - ratio) / 0.1  # 0.9..1.0 -> 15..0


def description_match(v: Vendor, req: RecommendRequest) -> float:
    text = v.description.lower()
    hits = sum(1 for stem in FORMAT_STEMS.get(req.event_format, []) if stem in text)
    return min(10.0, 5.0 * hits)


def language_match(v: Vendor, req: RecommendRequest) -> float:
    return 10.0 if req.language and req.language in v.languages else 0.0


def duration_fit(v: Vendor, req: RecommendRequest) -> float:
    if not req.duration_hours:
        return 0.0
    if v.max_hours is None:
        return 5.0  # присутствие не требуется
    return 10.0 if v.max_hours >= req.duration_hours else -10.0


def data_quality(v: Vendor, req: RecommendRequest) -> float:
    return -3.0 * v.price_imputed - 2.0 * v.city_imputed


COMPONENTS = {
    "specialization": specialization,
    "budget_fit": budget_fit,
    "description": description_match,
    "language": language_match,
    "duration": duration_fit,
    "data_quality": data_quality,
}


def rank(vendors: list[Vendor], req: RecommendRequest) -> list[Candidate]:
    candidates = []
    for v in vendors:
        parts = {name: round(fn(v, req), 2) for name, fn in COMPONENTS.items()}
        candidates.append(Candidate(vendor=v, score=round(sum(parts.values()), 2), score_parts=parts))
    return sorted(candidates, key=lambda c: (-c.score, c.vendor.id))
