"""Comparative facts across the shown cards.

DoD: "if names are erased, cards of one request cannot be confused". Facts from
matching.py describe each candidate alone; here we add one verified `compare` fact
per card saying what sets it apart from the other cards of the same answer.
Every statement is computed from catalog fields, so it stays checkable.
"""

from __future__ import annotations

from typing import Any

from matching import FORMAT_PLURAL, _key, _money

LANGUAGE_FORMS = {"русский": "русском", "казахский": "казахском", "английский": "английском"}


def _unique(values: list[Any], value: Any) -> bool:
    return values.count(value) == 1


def _differences(card: dict[str, Any], shown: list[dict[str, Any]], event_format: str) -> list[str]:
    prices = [c["price_from_kzt"] for c in shown]
    hours = [c["max_hours"] for c in shown if c.get("max_hours") is not None]
    leads = [bool(c["event_formats"]) and _key(c["event_formats"][0]) == event_format for c in shown]
    lead = leads[shown.index(card)]

    diffs = []
    if card["price_from_kzt"] == min(prices) and _unique(prices, min(prices)):
        diffs.append(f"самая низкая цена среди показанных: от {_money(card['price_from_kzt'])}")
    langs = [lang for lang in card["languages"]
             if sum(lang in c["languages"] for c in shown) == 1]
    if langs:
        forms = ", ".join(LANGUAGE_FORMS.get(_key(lang), lang) for lang in langs)
        diffs.append(f"среди показанных только этот вариант работает на {forms}")
    if lead and not all(leads):
        diffs.append(f"{FORMAT_PLURAL.get(event_format, event_format)} — основное направление "
                     "(в профиле стоят первыми)")
    if card.get("max_hours") is not None and hours and card["max_hours"] == max(hours) and _unique(hours, max(hours)):
        diffs.append(f"дольше всех на площадке: до {card['max_hours']} ч")
    if len(card["event_formats"]) == 1 and len(shown) > 1:
        only = _key(card["event_formats"][0])
        diffs.append(f"берёт только {FORMAT_PLURAL.get(only, only)}")
    return diffs


def add_comparative_facts(response: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of the response where each card gets a `compare` fact when it differs."""
    shown = response.get("results") or []
    if len(shown) < 2:
        return response
    event_format = _key(request.get("event_format"))
    results = []
    for card in shown:
        diffs = _differences(card, shown, event_format)[:2]
        facts = list(card["facts"])
        if diffs:
            text = "; ".join(diffs)
            facts.append({"id": "compare", "text": text[0].upper() + text[1:]})
        results.append({**card, "facts": facts})
    return {**response, "results": results}
