"""Grounded comparisons across the shown contractors, with optional LLM curation."""

from __future__ import annotations

import json
import os
from typing import Any, Callable
from urllib.request import Request, urlopen

from matching import _key, _money, profile_excerpts

ComparisonSelector = Callable[[dict[str, Any]], list[dict[str, Any]]]
QUALITY_IDS = {"price_estimated", "city_imputed", "synthetic"}
# Which advantage leads the reason: the rarest difference first.
HIGHLIGHT_ORDER = ("language_", "price_lowest", "hours_longest", "price_lower", "profile")


def _point(point_id: str, text: str) -> dict[str, str]:
    return {"id": point_id, "text": text}


def _fact(card: dict[str, Any], fact_id: str) -> str:
    return next((fact["text"] for fact in card.get("facts", []) if fact["id"] == fact_id), "")


def _quality_notes(card: dict[str, Any]) -> list[dict[str, str]]:
    notes = []
    if card.get("price_imputed"):
        notes.append(_point("price_estimated", "Цена в каталоге оценочная: её добавили при подготовке данных"))
    if card.get("city_imputed"):
        notes.append(_point("city_imputed", "Город добавлен при подготовке данных"))
    if card.get("synthetic"):
        notes.append(_point("synthetic", "Профиль создан для демонстрации"))
    return notes


def _distinct_profiles(shown: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Near-duplicate profiles (same template description) must not get the same quote.

    If a card's quote repeats another card's, take the first sentence of its own
    description that does not occur in the other shown descriptions.
    """
    quotes = [_fact(card, "profile") for card in shown]
    result = []
    for card, quote in zip(shown, quotes):
        if quote and quotes.count(quote) > 1:
            others = " ".join(" ".join(str(o.get("description", "")).split()) for o in shown if o is not card)
            unique = next((q for q in profile_excerpts(str(card.get("description", ""))) if q not in others), None)
            if unique:
                card = {**card, "facts": [
                    {**fact, "text": f"В описании профиля: «{unique}»"} if fact["id"] == "profile" else fact
                    for fact in card["facts"]
                ]}
        result.append(card)
    return result


def _comparison(card: dict[str, Any], shown: list[dict[str, Any]]) -> dict[str, list[dict[str, str]]]:
    pros: list[dict[str, str]] = []
    cons: list[dict[str, str]] = []
    price = card["price_from_kzt"]
    prices = [other["price_from_kzt"] for other in shown]
    cheapest, most_expensive = min(prices), max(prices)
    price_note = " (часть цен оценочная)" if any(
        other.get("price_imputed") for other in shown
    ) else ""

    if price == cheapest and prices.count(cheapest) == 1:
        pros.append(_point("price_lowest", f"Самая низкая цена среди показанных: от {_money(price)}{price_note}"))
    elif price < most_expensive:
        pros.append(_point("price_lower", f"Цена от {_money(price)} на {_money(most_expensive - price)} ниже самого дорогого варианта{price_note}"))
    if price > cheapest:
        cons.append(_point("price_higher", f"Цена от {_money(price)} на {_money(price - cheapest)} выше минимальной среди показанных{price_note}"))

    languages = {_key(language): language for language in card.get("languages", [])}
    for normalized, label in languages.items():
        if sum(normalized in {_key(value) for value in other.get("languages", [])} for other in shown) == 1:
            pros.append(_point(f"language_{normalized}", f"Язык «{label}» указан только у этого профиля среди показанных"))

    limits = [other["max_hours"] for other in shown if other.get("max_hours") is not None]
    limit = card.get("max_hours")
    if limit is not None and len(limits) >= 2:
        longest = max(limits)
        if limit == longest and limits.count(longest) == 1:
            pros.append(_point("hours_longest", f"Самый высокий указанный лимит среди сравнимых профилей: {limit} ч"))
        elif limit < longest:
            cons.append(_point("hours_shorter", f"Лимит {limit} ч на {longest - limit} ч меньше максимума среди показанных с лимитом"))

    profile = _fact(card, "profile")
    if profile and sum(_fact(other, "profile") == profile for other in shown) == 1:
        pros.append(_point("profile", profile))
    if not pros and (format_fact := _fact(card, "format")):
        pros.append(_point("format", format_fact))
    return {"pros": pros, "cons": cons + _quality_notes(card)}


def _valid_selection(selection: Any, options: dict[str, dict[str, list[dict[str, str]]]]) -> bool:
    if not isinstance(selection, list) or len(selection) != len(options):
        return False
    seen: set[str] = set()
    for item in selection:
        if not isinstance(item, dict) or set(item) != {"id", "pros", "cons"}:
            return False
        card_id, pros, cons = item["id"], item["pros"], item["cons"]
        if not isinstance(card_id, str) or card_id not in options or card_id in seen:
            return False
        if not all(isinstance(ids, list) and all(isinstance(value, str) for value in ids) for ids in (pros, cons)):
            return False
        if len(pros) > 2 or len(cons) > 2 or len(pros) != len(set(pros)) or len(cons) != len(set(cons)):
            return False
        available_pros = {point["id"] for point in options[card_id]["pros"]}
        available_cons = {point["id"] for point in options[card_id]["cons"]}
        if not pros or not set(pros).issubset(available_pros) or not set(cons).issubset(available_cons):
            return False
        seen.add(card_id)
    return seen == set(options)


def _selected(points: list[dict[str, str]], ids: list[str]) -> list[dict[str, str]]:
    by_id = {point["id"]: point for point in points}
    return [by_id[point_id] for point_id in ids]


def openai_compare(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Ask once about the whole TOP-3; return only point IDs."""
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    body = {
        "model": os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        "store": False,
        "input": [
            {"role": "developer", "content": (
                "Сравни всех показанных event-подрядчиков для запроса клиента. Для каждой "
                "карточки выбери 1–2 полезных плюса и 0–2 ограничения только из её ID. "
                "Учитывай бюджет, язык, длительность и различия описаний. Отсутствие языка "
                "в каталоге не доказывает, что подрядчик им не владеет. Не меняй порядок, "
                "не придумывай свойства. Верни JSON."
            )},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        "text": {"format": {
            "type": "json_schema", "name": "contractor_comparison", "strict": True,
            "schema": {
                "type": "object", "properties": {
                    "cards": {"type": "array", "items": {"type": "object", "properties": {
                        "id": {"type": "string"},
                        "pros": {"type": "array", "items": {"type": "string"}},
                        "cons": {"type": "array", "items": {"type": "string"}},
                    }, "required": ["id", "pros", "cons"], "additionalProperties": False}},
                }, "required": ["cards"], "additionalProperties": False,
            },
        }},
        "max_output_tokens": 350,
    }
    if body["model"].startswith(("gpt-4", "gpt-3")):
        body["temperature"] = 0  # same request -> same choice; reasoning models reject this field
    request = Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    with urlopen(request, timeout=6) as response:
        data = json.load(response)
    if data.get("status") != "completed":
        raise ValueError("model response was not completed")
    output_text = next(
        (part.get("text") for item in data.get("output", []) if item.get("type") == "message"
         for part in item.get("content", []) if part.get("type") == "output_text"), None,
    )
    return json.loads(output_text)["cards"]


def add_comparative_facts(
    response: dict[str, Any], request: dict[str, Any], selector: ComparisonSelector | None = None,
) -> dict[str, Any]:
    """Attach pros and cons; model output cannot change matching or factual text."""
    shown = response.get("results") or []
    if not shown:
        return response
    if len(shown) == 1:
        card = shown[0]
        pros = [_point(fact_id, _fact(card, fact_id)) for fact_id in ("format", "available", "budget") if _fact(card, fact_id)]
        return {**response, "results": [{**card, "comparison": {"pros": pros, "cons": _quality_notes(card)},
                                          "comparison_source": "catalog"}]}

    shown = _distinct_profiles(shown)
    options = {card["id"]: _comparison(card, shown) for card in shown}
    selected = {
        card_id: {
            "pros": points["pros"][:2],
            "cons": points["cons"][:2] + [point for point in points["cons"]
                                         if point["id"] in QUALITY_IDS and point not in points["cons"][:2]],
        }
        for card_id, points in options.items()
    }
    source = "fallback"
    if selector is not None or os.getenv("OPENAI_API_KEY"):
        try:
            payload = {
                "request": {key: request.get(key) for key in
                            ("category", "event_format", "budget_kzt", "language", "duration_hours")},
                "cards": [{"id": card["id"], "name": card["name"], "score": card["score"],
                           **options[card["id"]]} for card in shown],
            }
            choice = (selector or openai_compare)(payload)
            if _valid_selection(choice, options):
                selected = {}
                for item in choice:
                    card_id = item["id"]
                    notes = [point for point in options[card_id]["cons"] if point["id"] in QUALITY_IDS]
                    cons = _selected(options[card_id]["cons"], item["cons"])
                    selected[card_id] = {
                        "pros": _selected(options[card_id]["pros"], item["pros"]),
                        "cons": cons + [point for point in notes if point not in cons],
                    }
                source = "llm"
        except Exception:
            pass  # Network, JSON or validation errors keep the factual fallback.

    results = []
    for card in shown:
        comparison = selected[card["id"]]
        facts = list(card["facts"])
        # The reason says why the card is here: its main advantage. Limitations stay in
        # the "Что учесть" column. The format is already part of the shared checks.
        pros = sorted((point for point in comparison["pros"] if point["id"] != "format"),
                      key=lambda point: next((i for i, prefix in enumerate(HIGHLIGHT_ORDER)
                                              if point["id"].startswith(prefix)), len(HIGHLIGHT_ORDER)))
        highlighted = (pros or comparison["cons"])[:1]
        if highlighted:
            facts.append(_point("compare", highlighted[0]["text"]))
        results.append({**card, "comparison": comparison, "comparison_source": source, "facts": facts})
    return {**response, "results": results}
