"""Проверки из Definition of Done. Запуск: pytest -q"""
from datetime import date

import pytest

from app.catalog import load_catalog
from app.models import RecommendRequest, Status
from app.recommender import recommend


def req(**kw) -> RecommendRequest:
    base = dict(city="Алматы", date=date(2026, 10, 15), event_format="свадьба",
                category="Ведущий", budget_kzt=1_000_000)
    return RecommendRequest(**{**base, **kw})


def test_catalog_loads_all_rows():
    catalog = load_catalog()
    assert len(catalog) >= 66
    assert any(len(v.categories) > 1 for v in catalog)
    assert any(v.max_hours is None for v in catalog)


def test_multi_category_venue_is_found():
    r = recommend(req(category="Банкетный зал", budget_kzt=10_000_000, date=date(2026, 10, 1)))
    assert r.pool_size >= 7


def test_busy_vendor_never_shown():
    q = req()
    catalog = {v.id: v for v in load_catalog()}
    for card in recommend(q).results:
        assert q.date not in catalog[card.id].busy_dates


def test_over_budget_never_shown():
    q = req(budget_kzt=700_000)
    assert all(c.price_from_kzt <= 700_000 for c in recommend(q).results)


def test_format_filter():
    q = req(event_format="конференция")
    catalog = {v.id: v for v in load_catalog()}
    assert all("конференция" in catalog[c.id].event_formats for c in recommend(q).results)


def test_deterministic_order():
    q = req()
    first = [c.id for c in recommend(q).results]
    for _ in range(5):
        assert [c.id for c in recommend(q).results] == first


def test_different_dates_different_results():
    autumn = recommend(req(date=date(2026, 10, 15)))
    december = recommend(req(date=date(2026, 12, 19)))
    assert [c.id for c in autumn.results] != [c.id for c in december.results]
    assert "занят" in december.message


def test_category_not_found():
    r = recommend(req(city="Астана", category="Лайв-бэнд"))
    assert r.status == Status.CATEGORY_NOT_FOUND
    assert r.message and not r.results


def test_no_match_explained():
    r = recommend(req(city="Астана", category="Флорист", event_format="той", date=date(2026, 10, 14)))
    assert r.status == Status.NO_MATCH
    assert "не берёт" in r.message or "не берут" in r.message


def test_shortage_reason_when_less_than_three():
    r = recommend(req(city="Астана", category="Флорист", date=date(2026, 10, 14), budget_kzt=400_000))
    assert r.status == Status.MATCHED and len(r.results) < 3
    assert r.shortage_reason


@pytest.mark.parametrize("category", ["Ведущий", "Фотограф"])
def test_explanations_are_distinct(category):
    r = recommend(req(category=category, budget_kzt=2_000_000, event_format="корпоратив"))
    texts = [c.explanation for c in r.results]
    assert len(set(texts)) == len(texts)
