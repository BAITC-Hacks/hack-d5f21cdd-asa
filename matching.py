"""Deterministic matching over the anonymized HackAlem catalog."""

from __future__ import annotations

import csv
import re
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any, Iterable

DATA_PATH = Path(__file__).resolve().parent / "data" / "providers.csv"
LIST_FIELDS = ("categories", "event_formats", "languages", "busy_dates")
MIN_DATE, MAX_DATE = date(2026, 9, 23), date(2026, 12, 31)


def _list_field(value: Any) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [part.strip() for part in str(value or "").split("|") if part.strip()]


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().casefold() in {"true", "1", "yes", "да"}


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _key(value: Any) -> str:
    return str(value or "").strip().casefold()


def _money(value: int) -> str:
    return f"{value:,}".replace(",", " ") + " ₸"


def _display_date(value: date) -> str:
    months = ("января", "февраля", "марта", "апреля", "мая", "июня",
              "июля", "августа", "сентября", "октября", "ноября", "декабря")
    return f"{value.day} {months[value.month - 1]} {value.year} года"


def _profile_excerpt(description: str) -> str:
    """Return a short exact quote from the profile, without inventing claims."""
    clean = re.sub(r"\s+", " ", description).strip()
    for part in re.split(r"(?<=[.!?])\s+", clean):
        part = part.strip(" •\"«»")
        if 24 <= len(part) <= 160:
            return part.rstrip(".!?")
    # Some catalog descriptions have no sentence punctuation at all.
    return clean[:140].rsplit(" ", 1)[0].rstrip(".,;: ") if len(clean) >= 24 else ""


def load_providers(path: str | Path = DATA_PATH) -> list[dict[str, Any]]:
    with Path(path).open("r", encoding="utf-8-sig", newline="") as source:
        providers = []
        for raw in csv.DictReader(source):
            item: dict[str, Any] = dict(raw)
            for field in LIST_FIELDS:
                item[field] = _list_field(item.get(field))
            for field in ("price_from_kzt", "max_hours"):
                item[field] = _as_int(item.get(field))
            for field in ("price_imputed", "synthetic", "city_imputed"):
                item[field] = _as_bool(item.get(field))
            providers.append(item)
    return providers


def _fact(fact_id: str, text: str) -> dict[str, str]:
    return {"id": fact_id, "text": text}


def _empty_message(counts: Counter[str], total: int) -> str:
    if not total:
        return "В этом городе нет подрядчиков выбранной категории."
    causes = [f"{counts[key]} {label}" for key, label in (
        ("busy", "заняты на дату"),
        ("over_budget", "имеют цену от выше бюджета"),
        ("format", "не берут этот формат"),
        ("duration", "не подходят по длительности"),
        ("price_missing", "не имеют указанной цены"),
    ) if counts[key]]
    noun = "подрядчик" if total % 10 == 1 and total % 100 != 11 else ("подрядчика" if total % 10 in (2, 3, 4) and total % 100 not in (12, 13, 14) else "подрядчиков")
    return f"В городе найдено {total} {noun} категории, но ни один не прошёл условия: " + ", ".join(causes) + "."


def recommend(request: dict[str, Any], providers: Iterable[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Return top three, verified facts and rejection counts. Counters can overlap."""
    city, category = _key(request.get("city")), _key(request.get("category"))
    event_format = _key(request.get("event_format"))
    budget = _as_int(request.get("budget_kzt"))
    date_text = str(request.get("date") or "").strip()
    duration_raw = request.get("duration_hours")
    duration = None if duration_raw in (None, "") else _as_int(duration_raw)
    language = _key(request.get("language"))
    if not all((city, category, event_format, date_text)) or budget is None or budget < 0:
        raise ValueError("city, date, event_format, category and nonnegative integer budget_kzt are required")
    try:
        event_date = date.fromisoformat(date_text)
    except ValueError as exc:
        raise ValueError("date must be ISO YYYY-MM-DD") from exc
    if not MIN_DATE <= event_date <= MAX_DATE:
        raise ValueError("date is outside the catalog calendar (2026-09-23 to 2026-12-31)")
    if duration_raw not in (None, "") and (duration is None or duration <= 0):
        raise ValueError("duration_hours must be a positive integer")

    catalog = list(providers) if providers is not None else load_providers()
    candidates = [p for p in catalog if _key(p.get("city")) == city
                  and category in {_key(x) for x in _list_field(p.get("categories"))}]
    counts: Counter[str] = Counter()
    matches = []
    for provider in candidates:
        price = _as_int(provider.get("price_from_kzt"))
        max_hours = _as_int(provider.get("max_hours"))
        formats = {_key(x) for x in _list_field(provider.get("event_formats"))}
        languages = {_key(x) for x in _list_field(provider.get("languages"))}
        rejected = {
            "busy": date_text in set(_list_field(provider.get("busy_dates"))),
            "over_budget": price is not None and price > budget,
            "format": event_format not in formats,
            "duration": duration is not None and max_hours is not None and duration > max_hours,
            "price_missing": price is None,
        }
        for cause, failed in rejected.items():
            if failed:
                counts[cause] += 1
        if any(rejected.values()):
            continue

        name = str(provider.get("anon_name") or provider.get("name") or "")
        synthetic = _as_bool(provider.get("synthetic"))
        price_imputed = _as_bool(provider.get("price_imputed"))
        city_imputed = _as_bool(provider.get("city_imputed"))
        excerpt = _profile_excerpt(str(provider.get("description") or ""))
        category_label = next(x for x in _list_field(provider.get("categories")) if _key(x) == category)
        facts = [
            _fact("category", f"Категория: {category_label}"),
            _fact("available", f"По календарю свободен {_display_date(event_date)}"),
            _fact("format", f"Берёт мероприятия формата «{event_format}»"),
            _fact("budget", f"{'Оценочная цена' if price_imputed else 'Цена'} от {_money(price)} укладывается в бюджет {_money(budget)}"),
        ]
        if language and language in languages:
            facts.append(_fact("language", f"Работает на языке «{language}»"))
        if duration is not None and max_hours is not None:
            facts.append(_fact("duration", f"Работает до {max_hours} ч, запрос на {duration} ч"))
        if excerpt:
            facts.append(_fact("profile", f"В описании профиля: «{excerpt}»"))
        if synthetic:
            facts.append(_fact("synthetic", "Профиль полностью синтетический, создан для демонстрации"))
        if city_imputed:
            facts.append(_fact("city_imputed", "Город указан при подготовке датасета"))

        description = _key(provider.get("description"))
        score = 70 + (10 if event_format in description else 0)
        score += 5 if language and language in languages else 0
        score += 5 if duration is not None and max_hours is not None else 0
        matches.append({
            "id": str(provider.get("id") or ""), "name": name,
            "categories": _list_field(provider.get("categories")),
            "city": str(provider.get("city") or ""), "price_from_kzt": price,
            "price_imputed": price_imputed, "city_imputed": city_imputed,
            "synthetic": synthetic, "description": str(provider.get("description") or ""),
            "event_formats": _list_field(provider.get("event_formats")),
            "languages": _list_field(provider.get("languages")), "max_hours": max_hours,
            "score": score, "facts": facts,
        })

    matches.sort(key=lambda item: (-item["score"], item["id"]))
    results = matches[:3]
    summary = {"category_candidates": len(candidates)}
    summary.update({key: counts[key] for key in ("busy", "over_budget", "format", "duration", "price_missing")})
    if results:
        count = len(matches)
        shown = len(results)
        message = f"Подходящих подрядчиков: {count}. "
        message += f"Показан {shown}." if shown == 1 else f"Показаны первые {shown}."
        if count < 3:
            if len(candidates) > count:
                message += " Остальные не прошли условия: " + ", ".join(
                    f"{counts[key]} {label}" for key, label in (
                        ("busy", "заняты на дату"), ("over_budget", "имеют цену от выше бюджета"),
                        ("format", "не берут формат"), ("duration", "не подходят по длительности"),
                        ("price_missing", "не имеют указанной цены"),
                    ) if counts[key]
                ) + "."
            else:
                message += " В городе больше профилей этой категории нет."
        status = "matched"
    else:
        message = _empty_message(counts, len(candidates))
        status = "category_not_found" if not candidates else "no_match"
    return {"status": status, "results": results, "message": message, "counts": summary}
