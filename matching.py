"""Deterministic provider matching for the HackAlem MVP."""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path
from typing import Any, Iterable


DATA_PATH = Path(__file__).resolve().parent / "data" / "providers.csv"
LIST_FIELDS = ("categories", "event_formats", "languages", "busy_dates")


def _list_field(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [part.strip() for part in str(value or "").split("|") if part.strip()]


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().casefold() in {"true", "1", "yes", "да"}


def _as_int(value: Any) -> int | None:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None


def load_providers(path: str | Path = DATA_PATH) -> list[dict[str, Any]]:
    """Read and normalize the source CSV. The CSV stays the source of truth."""
    with Path(path).open("r", encoding="utf-8-sig", newline="") as source:
        rows = csv.DictReader(source)
        providers: list[dict[str, Any]] = []
        for raw in rows:
            item: dict[str, Any] = dict(raw)
            for field in LIST_FIELDS:
                item[field] = _list_field(item.get(field))
            item["price_from_kzt"] = _as_int(item.get("price_from_kzt"))
            item["max_hours"] = _as_int(item.get("max_hours"))
            for field in ("price_imputed", "synthetic", "city_imputed"):
                item[field] = _as_bool(item.get(field))
            providers.append(item)
        return providers


def _key(value: Any) -> str:
    return str(value or "").strip().casefold()


def _display_date(value: str) -> str:
    try:
        parsed = date.fromisoformat(value)
        months = (
            "января", "февраля", "марта", "апреля", "мая", "июня",
            "июля", "августа", "сентября", "октября", "ноября", "декабря",
        )
        return f"{parsed.day} {months[parsed.month - 1]} {parsed.year} года"
    except (ValueError, TypeError):
        return value


def _score(price: int, budget: int) -> int:
    # All hard requirements contribute the same base. Remaining budget is a
    # small, reproducible preference, capped at 15 points.
    if budget <= 0:
        return 70
    return 70 + min(15, max(0, 15 * (budget - price) // budget))


def recommend(request: dict[str, Any], providers: Iterable[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Return up to three providers matching city, date, format, category and budget.

    `providers` is an optional injection point for callers and local checks;
    production calls load the bundled CSV.
    """
    catalog = list(providers) if providers is not None else load_providers()
    city = _key(request.get("city"))
    category = _key(request.get("category"))
    event_format = _key(request.get("event_format"))
    event_date = str(request.get("date") or "").strip()
    budget = _as_int(request.get("budget_kzt"))
    if not all((city, category, event_format, event_date)) or budget is None:
        raise ValueError("request must include city, date, event_format, category and numeric budget_kzt")

    in_city = [provider for provider in catalog if _key(provider.get("city")) == city]
    in_category = [
        provider for provider in in_city
        if category in {_key(value) for value in _list_field(provider.get("categories"))}
    ]
    if not in_category:
        return {"status": "category_not_found", "results": []}

    matches: list[dict[str, Any]] = []
    for provider in in_category:
        price = provider.get("price_from_kzt")
        if not isinstance(price, int):
            price = _as_int(price)
        formats = _list_field(provider.get("event_formats"))
        busy_dates = _list_field(provider.get("busy_dates"))
        if (
            price is None
            or _key(event_format) not in {_key(value) for value in formats}
            or event_date in busy_dates
            or price > budget
        ):
            continue

        name = provider.get("anon_name") or provider.get("name") or ""
        price_imputed = _as_bool(provider.get("price_imputed"))
        synthetic = _as_bool(provider.get("synthetic"))
        facts = []
        # Synthetic rows are useful for exercising matching, but their field
        # values are not evidence about a real provider.
        if not synthetic:
            price_text = (
                f"В каталоге указана оценочная цена от {price} ₸, бюджет {budget} ₸"
                if price_imputed
                else f"Цена от {price} ₸, бюджет {budget} ₸"
            )
            facts = [
                {"id": "available", "text": f"Не занят {_display_date(event_date)}"},
                {"id": "format", "text": f"Работает с мероприятиями формата «{event_format}»"},
                {"id": "budget", "text": price_text},
            ]
        matches.append({
            "id": str(provider.get("id", "")),
            "name": str(name),
            "categories": _list_field(provider.get("categories")),
            "city": str(provider.get("city", "")),
            "price_from_kzt": price,
            "price_imputed": price_imputed,
            "synthetic": synthetic,
            "score": _score(price, budget),
            "facts": facts,
        })

    matches.sort(key=lambda item: (-item["score"], item["id"]))
    results = matches[:3]
    return {"status": "matched" if results else "no_match", "results": results}
