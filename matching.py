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
REJECT_LABELS = (
    ("busy", "заняты на дату"),
    ("over_budget", "имеют цену от выше бюджета"),
    ("format", "не берут этот формат"),
    ("duration", "не подходят по длительности"),
    ("price_missing", "не имеют указанной цены"),
)
# Word stems, because descriptions say «свадеб», «корпоративных», not the exact format name.
FORMAT_STEMS = {
    "свадьба": ("свадеб", "свадьб", "молодожён", "молодожен", "невест", "бракосочет"),
    "той": ("тоев", "тоя", "национальн", "казахск", "традиц", "обряд"),
    "корпоратив": ("корпоратив", "бизнес", "компани", "бренд", "тимбилдинг"),
    "конференция": ("конференц", "форум", "делов", "презентац", "бизнес"),
    "юбилей": ("юбиле",),
    "день рождения": ("день рождения", "дня рождения", "детск"),
}


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


def _display_day(value: date) -> str:
    months = ("января", "февраля", "марта", "апреля", "мая", "июня",
              "июля", "августа", "сентября", "октября", "ноября", "декабря")
    return f"{value.day} {months[value.month - 1]}"


def _display_date(value: date) -> str:
    return f"{_display_day(value)} {value.year} года"


def _profile_excerpt(description: str) -> str:
    """Return a short exact quote from the profile, without inventing claims."""
    clean = re.sub(r"\s+", " ", description).strip()
    for part in re.split(r"(?<=[.!?])\s+", clean):
        part = part.strip(" •\"«»")
        if 24 <= len(part) <= 160:
            return part.rstrip(".!?")
    # Some catalog descriptions have no sentence punctuation at all ("…13 лет Вел свадьбы…"):
    # treat "lowercase/digit + space + Capitalized word" as a sentence boundary.
    for part in re.split(r"(?<=[а-яёa-z0-9])\s+(?=[А-ЯЁA-Z][а-яёa-z])", clean):
        part = part.strip(" •\"«»-—")
        if 24 <= len(part) <= 160:
            return part.rstrip(".!?,;:")
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


def _score_parts(event_format: str, formats_ordered: list[str], description: str,
                 language_match: bool | None, duration_confirmed: bool) -> dict[str, int]:
    """Relevance on top of the base 70. Price never adds points: cheaper is not better."""
    position = formats_ordered.index(event_format) if event_format in formats_ordered else len(formats_ordered)
    stems = FORMAT_STEMS.get(event_format, (event_format,))
    hits = sum(1 for stem in set(stems) if stem in description)
    return {
        # The format listed first is the provider's main line of work.
        "specialization": max(0, 10 - 4 * position),
        # The profile text itself talks about this kind of event.
        "description": min(10, 5 * hits),
        "language": 5 if language_match else 0,
        "duration": 5 if duration_confirmed else 0,
    }


def _fact(fact_id: str, text: str) -> dict[str, str]:
    return {"id": fact_id, "text": text}


def _plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    return few if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14) else many


def _causes(counts: Counter[str], event_date: date) -> str:
    """«заняты на 19 декабря — 9, имеют цену от выше бюджета — 2». Counters can overlap."""
    labels = dict(REJECT_LABELS, busy=f"заняты на {_display_day(event_date)}")
    return ", ".join(f"{labels[key]} — {counts[key]}" for key, _ in REJECT_LABELS if counts[key])


def _next_free(provider: dict[str, Any], event_date: date) -> date | None:
    busy = set(_list_field(provider.get("busy_dates")))
    day = event_date
    while day < MAX_DATE:
        day = date.fromordinal(day.toordinal() + 1)
        if day.isoformat() not in busy:
            return day
    return None


def _empty_message(counts: Counter[str], total: int, where: str, event_date: date,
                   busy_only: list[dict[str, Any]]) -> str:
    noun = _plural(total, "подрядчик", "подрядчика", "подрядчиков")
    message = f"{where}: есть {total} {noun}, но ни один не прошёл условия: {_causes(counts, event_date)}."
    # Candidates that failed ONLY because of the date: suggest the nearest free day.
    options = sorted((day, str(p.get("id")), str(p.get("anon_name")))
                     for p in busy_only if (day := _next_free(p, event_date)))
    if options:
        day, _, name = options[0]
        message += f" Ближайший вариант по остальным условиям: {name} свободен {_display_date(day)}."
    return message


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
    busy_only: list[dict[str, Any]] = []
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
            if [cause for cause, failed in rejected.items() if failed] == ["busy"]:
                busy_only.append(provider)
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

        formats_ordered = [_key(x) for x in _list_field(provider.get("event_formats"))]
        score_parts = _score_parts(event_format, formats_ordered, _key(provider.get("description")),
                                   language in languages if language else None,
                                   duration is not None and max_hours is not None)
        score = 70 + sum(score_parts.values())
        matches.append({
            "id": str(provider.get("id") or ""), "name": name,
            "categories": _list_field(provider.get("categories")),
            "city": str(provider.get("city") or ""), "price_from_kzt": price,
            "price_imputed": price_imputed, "city_imputed": city_imputed,
            "synthetic": synthetic, "description": str(provider.get("description") or ""),
            "event_formats": _list_field(provider.get("event_formats")),
            "languages": _list_field(provider.get("languages")), "max_hours": max_hours,
            "score": score, "score_parts": score_parts, "facts": facts,
        })

    matches.sort(key=lambda item: (-item["score"], item["id"]))
    results = matches[:3]
    summary = {"category_candidates": len(candidates)}
    summary.update({key: counts[key] for key in ("busy", "over_budget", "format", "duration", "price_missing")})
    where = f"«{str(request.get('category')).strip()}» в городе {str(request.get('city')).strip()}"
    if results:
        count, shown, total = len(matches), len(results), len(candidates)
        message = f"Подходящих подрядчиков: {count} из {total} в категории {where}. "
        message += f"Показан {shown}." if shown == 1 else f"Показаны первые {shown}."
        if total > count:
            prefix = "Остальные не прошли условия" if count < 3 else "Не прошли условия"
            message += f" {prefix}: {_causes(counts, event_date)}."
        elif count < 3:
            message += " В городе больше профилей этой категории нет."
        status = "matched"
    elif candidates:
        message = _empty_message(counts, len(candidates), f"Категория {where}", event_date, busy_only)
        status = "no_match"
    else:
        elsewhere = Counter(str(p.get("city")) for p in catalog
                            if category in {_key(x) for x in _list_field(p.get("categories"))})
        message = f"В каталоге нет подрядчиков категории {where}."
        if elsewhere:
            message += " Эта категория есть: " + ", ".join(f"{c} — {n}" for c, n in sorted(elsewhere.items())) + "."
        status = "category_not_found"
    return {"status": status, "results": results, "message": message, "counts": summary}
