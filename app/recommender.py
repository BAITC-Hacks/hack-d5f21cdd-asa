"""[Участник 3] Оркестратор пайплайна: единственная точка входа recommend(request).

catalog -> pool(город+категория) -> hard filters -> rank -> top-3 -> facts -> LLM|template -> validate
"""
from __future__ import annotations

import time
from collections import Counter
from datetime import timedelta

from app.catalog import load_catalog
from app.config import MAX_RESULTS, USE_LLM
from app.explain.facts import build_facts
from app.explain.llm import build_prompt, llm_explanations
from app.explain.templates import template_explanation
from app.explain.validator import is_valid, too_similar
from app.matching.filters import apply_filters, fmt_date, select_pool
from app.matching.ranking import rank
from app.models import (CALENDAR_END, Card, Candidate, RecommendRequest, RecommendResponse,
                        Rejection, RejectReason, Status)

# (форма для 1, форма для 2+)
REASON_TEXT = {
    RejectReason.BUSY: ("занят {date}", "заняты {date}"),
    RejectReason.FORMAT: ("не берёт формат «{fmt}»", "не берут формат «{fmt}»"),
    RejectReason.BUDGET: ("дороже бюджета", "дороже бюджета"),
    RejectReason.LANGUAGE: ("не работает на языке «{lang}»", "не работают на языке «{lang}»"),
}


def plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def summarize_rejections(rejections: list[Rejection], req: RecommendRequest) -> str:
    counts = Counter(r for rej in rejections for r in rej.reasons)
    parts = []
    for reason in RejectReason:
        if counts[reason]:
            one, many = REASON_TEXT[reason]
            form = plural(counts[reason], one, many, many)
            text = form.format(date=fmt_date(req.date), fmt=req.event_format, lang=req.language)
            parts.append(f"{counts[reason]} {text}")
    return ", ".join(parts)


def next_free_hint(rejections: list[Rejection], req: RecommendRequest) -> str:
    """Для тех, кто отсеян ТОЛЬКО из-за занятости: ближайшая свободная дата."""
    catalog = {v.id: v for v in load_catalog()}
    best = None
    for rej in rejections:
        if rej.reasons != [RejectReason.BUSY]:
            continue
        v, d = catalog[rej.vendor_id], req.date + timedelta(days=1)
        while d <= CALENDAR_END and d in v.busy_dates:
            d += timedelta(days=1)
        if d <= CALENDAR_END and (best is None or (d, v.id) < best[:2]):
            best = (d, v.id, v.name)
    return f" Ближайший вариант: {best[2]} свободен {fmt_date(best[0])}." if best else ""


def explain(shown: list[Candidate], req: RecommendRequest) -> list[tuple[str, str]]:
    build_facts(shown, req)
    templates = [template_explanation(c) for c in shown]
    llm = llm_explanations(shown, req) if USE_LLM else None
    request_text = build_prompt([], req)

    out: list[tuple[str, str]] = []
    for c, tpl in zip(shown, templates):
        text = (llm or {}).get(c.vendor.id, "")
        ok, _ = is_valid(text, c, request_text) if text else (False, "нет")
        if ok and not any(too_similar(text, prev) for prev, _ in out):
            out.append((text, "llm"))
        else:
            out.append((tpl, "template"))
    return out


def recommend(req: RecommendRequest) -> RecommendResponse:
    t0 = time.perf_counter()
    catalog = load_catalog()
    pool = select_pool(catalog, req)
    cat_city = f"«{req.category}» в городе {req.city}"

    def done(**kw) -> RecommendResponse:
        return RecommendResponse(elapsed_ms=int((time.perf_counter() - t0) * 1000), **kw)

    if not pool:
        elsewhere = Counter(v.city for v in catalog if req.category in v.categories)
        hint = ", ".join(f"{c} — {n}" for c, n in sorted(elsewhere.items()))
        return done(
            status=Status.CATEGORY_NOT_FOUND,
            message=f"В каталоге нет подрядчиков категории {cat_city}."
                    + (f" Эта категория есть в других городах: {hint}." if hint else ""),
            shortage_reason=None, pool_size=0, passed=0, results=[], rejections=[],
        )

    passed, rejections = apply_filters(pool, req)
    n = len(pool)
    pool_text = f"{n} {plural(n, 'профиль', 'профиля', 'профилей')} категории {cat_city}"
    of_pool = f"{n} {plural(n, 'профиля', 'профилей', 'профилей')} категории {cat_city}"  # «из N ...»

    if not passed:
        return done(
            status=Status.NO_MATCH,
            message=f"Есть {pool_text}, но ни один не подходит: {summarize_rejections(rejections, req)}."
                    + next_free_hint(rejections, req),
            shortage_reason=None, pool_size=n, passed=0, results=[], rejections=rejections,
        )

    shown = rank(passed, req)[:MAX_RESULTS]
    explanations = explain(shown, req)
    cards = [
        Card(
            id=c.vendor.id, name=c.vendor.name, categories=c.vendor.categories, city=c.vendor.city,
            price_from_kzt=c.vendor.price_from_kzt, price_imputed=c.vendor.price_imputed,
            city_imputed=c.vendor.city_imputed, synthetic=c.vendor.synthetic,
            score=c.score, score_parts=c.score_parts,
            explanation=text, explanation_source=source, facts=c.facts,
        )
        for c, (text, source) in zip(shown, explanations)
    ]

    busy = sum(1 for r in rejections if RejectReason.BUSY in r.reasons)
    message = f"Подобрали {len(cards)} из {of_pool}."
    if busy:
        message += f" На {fmt_date(req.date)} {plural(busy, 'занят', 'заняты', 'заняты')} {busy} из {n}."

    shortage = None
    if len(cards) < MAX_RESULTS:
        if n < MAX_RESULTS:
            shortage = f"В каталоге всего {pool_text}."
            if rejections:
                shortage += f" Из них отсеяны: {summarize_rejections(rejections, req)}."
        else:
            shortage = f"Остальные отсеяны: {summarize_rejections(rejections, req)}."

    return done(status=Status.MATCHED, message=message, shortage_reason=shortage,
                pool_size=n, passed=len(passed), results=cards, rejections=rejections)
