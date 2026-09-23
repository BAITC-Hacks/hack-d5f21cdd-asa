"""[Участник 2] Сбор ПРОВЕРЯЕМЫХ фактов для объяснения.

Главная идея: факты сравнительные — «самый доступный из трёх», «единственный с казахским».
Именно они делают объяснения невзаимозаменяемыми (требование DoD).
Каждый факт — короткая фраза, которую можно проверить по полям профиля.

Порядок в списке = приоритет: сначала отличающие факты, потом общие.

TODO(У2): улучшить извлечение из описания (сейчас — регэкспы на числа и рейтинги).
"""
from __future__ import annotations

import re

from app.matching.filters import fmt_date, fmt_kzt
from app.models import Candidate, RecommendRequest

HIGHLIGHT_PATTERNS = [
    r"опыт[а-я]*\s+(?:ведения\s+[а-я]+\s+)?(?:работы\s+)?(?:более\s+)?\d+\s*(?:лет|года)",
    r"\d[\d\s]*\+?\s*(?:свадеб|мероприятий|съёмок|съемок|заказов|человек|тыс\s+зрителей)",
    r"(?:топ|ТОП|TOP)[-\s]?\d+(?:\s+[^\s.,;•!]+){0,3}",
    r"(?:более|около)\s+\d+\s+лет",
    r"вместимость[^.,;]{0,30}",
    r"\d+\s+вокалист[а-я]*",
]


def description_highlights(text: str, limit: int = 2) -> list[str]:
    found: list[str] = []
    for pattern in HIGHLIGHT_PATTERNS:
        for m in re.finditer(pattern, text, flags=re.IGNORECASE):
            snippet = " ".join(m.group(0).split()).strip(" ,.;")
            if snippet and all(snippet.lower() not in f.lower() for f in found):
                found.append(snippet)
            if len(found) >= limit:
                return found
    return found


def build_facts(shown: list[Candidate], req: RecommendRequest) -> None:
    """Заполняет candidate.facts для каждой карточки выдачи (in-place)."""
    prices = [c.vendor.price_from_kzt for c in shown]
    hours = [c.vendor.max_hours for c in shown if c.vendor.max_hours is not None]
    many = len(shown) > 1

    for c in shown:
        v = c.vendor
        distinct: list[str] = []
        common: list[str] = []

        # Цена — сравнительно, если карточек несколько
        share = round(100 * v.price_from_kzt / req.budget_kzt)
        price_fact = f"цена от {fmt_kzt(v.price_from_kzt)} — {share}% бюджета"
        if many and v.price_from_kzt == min(prices) and prices.count(min(prices)) == 1:
            distinct.append(f"самый доступный в подборке: {price_fact}")
        elif many and v.price_from_kzt == max(prices) and prices.count(max(prices)) == 1:
            distinct.append(f"самый дорогой в подборке, но укладывается: {price_fact}")
        else:
            common.append(price_fact)
        if v.price_imputed:
            common.append("цена ориентировочная (проставлена при подготовке данных)")

        # Специализация на формате
        idx = v.event_formats.index(req.event_format)
        if idx == 0:
            distinct.append(f"формат «{req.event_format}» указан в профиле первым")
        elif len(v.event_formats) >= 4:
            common.append(f"универсал: берёт {len(v.event_formats)} {'формата' if len(v.event_formats) < 5 else 'форматов'}, включая «{req.event_format}»")

        # Языки — уникальные среди показанных
        for lang in v.languages:
            owners = sum(1 for o in shown if lang in o.vendor.languages)
            if many and owners == 1:
                distinct.append(f"единственный в подборке работает на языке: {lang}")
        if req.language:
            common.append(f"работает на языке: {req.language}")

        # Длительность
        if v.max_hours is not None:
            if req.duration_hours:
                if v.max_hours >= req.duration_hours:
                    common.append(f"на площадке до {v.max_hours} ч — покрывает ваши {req.duration_hours} ч")
                else:
                    distinct.append(f"на площадке только до {v.max_hours} ч из запрошенных {req.duration_hours} ч")
            if many and hours and v.max_hours == max(hours) and hours.count(max(hours)) == 1:
                distinct.append(f"дольше всех в подборке на площадке: до {v.max_hours} ч")
        elif req.duration_hours:
            common.append("работа не привязана к часам на площадке")

        # Конкретика из описания
        for h in description_highlights(v.description):
            distinct.append(f"из описания: «{h}»")

        common.append(f"свободен {fmt_date(req.date)}")
        c.facts = distinct + common
