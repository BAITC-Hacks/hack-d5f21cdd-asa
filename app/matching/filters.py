"""[Участник 1] Жёсткие фильтры.

Шаг 1: пул = город + категория (категория ищется ВХОЖДЕНИЕМ: «Банкетный зал|Отель|Ресторан»).
        Пустой пул -> статус CATEGORY_NOT_FOUND.
Шаг 2: каждый профиль пула проверяется по ВСЕМ условиям, причины копятся (профиль может
        провалить несколько). Счётчики для сообщения считаются по всем причинам.

Решения команды (менять осознанно и отражать в README):
- язык, если указан, — жёсткое условие;
- длительность — НЕ фильтр, а фактор ранжирования (max_hours=None не провал).
"""
from __future__ import annotations

from app.models import RecommendRequest, Rejection, RejectReason, Vendor


def fmt_kzt(value: int) -> str:
    return f"{value:,}".replace(",", " ") + " ₸"


def fmt_date(d) -> str:
    months = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля",
              "августа", "сентября", "октября", "ноября", "декабря"]
    return f"{d.day} {months[d.month - 1]}"


def select_pool(catalog, req: RecommendRequest) -> list[Vendor]:
    return [v for v in catalog if v.city == req.city and req.category in v.categories]


def check(v: Vendor, req: RecommendRequest) -> tuple[list[RejectReason], list[str]]:
    reasons, details = [], []
    if req.date in v.busy_dates:
        reasons.append(RejectReason.BUSY)
        details.append(f"занят {fmt_date(req.date)}")
    if req.event_format not in v.event_formats:
        reasons.append(RejectReason.FORMAT)
        details.append(f"не берёт формат «{req.event_format}» (берёт: {', '.join(v.event_formats)})")
    if v.price_from_kzt > req.budget_kzt:
        reasons.append(RejectReason.BUDGET)
        details.append(f"цена от {fmt_kzt(v.price_from_kzt)} при бюджете {fmt_kzt(req.budget_kzt)}")
    if req.language and req.language not in v.languages:
        reasons.append(RejectReason.LANGUAGE)
        details.append(f"не работает на языке «{req.language}»")
    return reasons, details


def apply_filters(pool: list[Vendor], req: RecommendRequest) -> tuple[list[Vendor], list[Rejection]]:
    passed, rejected = [], []
    for v in pool:
        reasons, details = check(v, req)
        if reasons:
            rejected.append(Rejection(vendor_id=v.id, vendor_name=v.name,
                                      reasons=reasons, detail="; ".join(details)))
        else:
            passed.append(v)
    return passed, rejected
