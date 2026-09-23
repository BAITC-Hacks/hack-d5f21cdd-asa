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
REJECT_CAUSES = ("busy", "over_budget", "format", "duration", "price_missing")
FORMAT_PLURAL = {
    "свадьба": "свадьбы", "той": "тои", "корпоратив": "корпоративы",
    "конференция": "конференции", "юбилей": "юбилеи", "день рождения": "дни рождения",
}
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


def _next_free(provider: dict[str, Any], event_date: date) -> date | None:
    busy = set(_list_field(provider.get("busy_dates")))
    day = event_date
    while day < MAX_DATE:
        day = date.fromordinal(day.toordinal() + 1)
        if day.isoformat() not in busy:
            return day
    return None


def _count(n: int) -> str:
    return f"{n} {_plural(n, 'подрядчик', 'подрядчика', 'подрядчиков')}"


def _name(provider: dict[str, Any]) -> str:
    return str(provider.get("anon_name") or provider.get("name") or provider.get("id"))


def _added(group: list[dict[str, Any]]) -> str:
    return f"добавится {_name(group[0])}" if len(group) == 1 else f"добавятся {len(group)}"


def _hints(blocked: list[tuple[dict[str, Any], set[str]]], event_date: date, event_format: str,
           budget: int, duration: int | None) -> list[str]:
    """Plain-language advice: what to change to get more options.

    Only providers blocked by exactly ONE condition are counted per condition, so each
    hint is honest: changing that condition alone really adds those providers.
    Wording avoids gendered forms: names do not tell a provider's gender.
    """
    single = {cause: [p for p, causes in blocked if causes == {cause}] for cause in REJECT_CAUSES}
    several = [causes for _, causes in blocked if len(causes) > 1]
    day = _display_day(event_date)
    hints = []

    if group := single["busy"]:
        free: dict[date, list[str]] = {}
        for p in sorted(group, key=lambda p: str(p.get("id"))):
            if next_day := _next_free(p, event_date):
                free.setdefault(next_day, []).append(_name(p))
        if len(group) == 1:
            text = f"{_name(group[0])} подходит по всем условиям, кроме даты: {day} уже занято."
            if free:
                text += f" Ближайший свободный день — {_display_day(min(free))}."
        else:
            text = f"{_count(len(group))} подходят по всем условиям, кроме даты: {day} у них занято."
            if free:
                days = sorted(free)[:3]
                text += " Ближайшие свободные дни: " + ", ".join(
                    f"{_display_day(d)} ({', '.join(free[d])})" for d in days) + "."
        hints.append(text)

    if group := sorted(single["over_budget"], key=lambda p: (_as_int(p.get("price_from_kzt")), str(p.get("id")))):
        cheapest = _as_int(group[0].get("price_from_kzt"))
        top = _as_int(group[-1].get("price_from_kzt"))
        if len(group) == 1:
            text = (f"{_name(group[0])} подходит по всем условиям, кроме бюджета: цена от {_money(cheapest)}. "
                    f"Увеличьте бюджет на {_money(cheapest - budget)}, чтобы добавить этот вариант.")
        else:
            first = [p for p in group if _as_int(p.get("price_from_kzt")) == cheapest]
            text = (f"{_count(len(group))} подходят по всем условиям, кроме бюджета. "
                    f"Увеличьте бюджет на {_money(cheapest - budget)} (до {_money(cheapest)}) — {_added(first)}.")
            if top > cheapest:
                everyone = "оба" if len(group) == 2 else f"все {len(group)}"
                text += f" При бюджете {_money(top)} подойдут {everyone}."
        hints.append(text)

    if group := single["format"]:
        wanted = FORMAT_PLURAL.get(event_format, f"формат «{event_format}»")
        their = Counter(_key(f) for p in group for f in _list_field(p.get("event_formats")))
        common = ", ".join(FORMAT_PLURAL.get(f, f) for f, _ in sorted(their.items(), key=lambda x: (-x[1], x[0]))[:3])
        who = _name(group[0]) if len(group) == 1 else _count(len(group))
        verb = "не берёт" if len(group) == 1 else "не берут"
        hints.append(f"{who} {verb} {wanted} — только {common}.")

    if group := single["duration"]:
        longest = max(_as_int(p.get("max_hours")) for p in group)
        if len(group) == 1:
            hints.append(f"{_name(group[0])} работает на площадке максимум {longest} ч. "
                         f"Сократите длительность до {longest} ч, чтобы добавить этот вариант.")
        else:
            fit = [p for p in group if _as_int(p.get("max_hours")) == longest]
            hints.append(f"{_count(len(group))} работают на площадке меньше {duration} ч. "
                         f"Сократите длительность до {longest} ч — {_added(fit)}.")

    if group := single["price_missing"]:
        hints.append(f"У {len(group)} {_plural(len(group), 'подрядчика', 'подрядчиков', 'подрядчиков')} "
                     "не указана цена, их нельзя сравнить с бюджетом.")

    if several:
        names = {"busy": "дата", "over_budget": "бюджет", "format": "тип мероприятия",
                 "duration": "длительность", "price_missing": "цена"}
        which = ", ".join(names[c] for c in REJECT_CAUSES if any(c in s for s in several))
        verb = "не подходит" if len(several) % 10 == 1 and len(several) % 100 != 11 else "не подходят"
        hints.append(f"Ещё {_count(len(several))} {verb} сразу по нескольким условиям ({which}).")
    return hints


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
        raise ValueError("Заполните город, дату, тип мероприятия, категорию и бюджет (целое число от 0).")
    try:
        event_date = date.fromisoformat(date_text)
    except ValueError as exc:
        raise ValueError("Дата должна быть в формате ГГГГ-ММ-ДД, например 2026-10-15.") from exc
    if not MIN_DATE <= event_date <= MAX_DATE:
        raise ValueError("Календарь подрядчиков есть только с 23 сентября по 31 декабря 2026 года — выберите дату в этом окне.")
    if duration_raw not in (None, "") and (duration is None or duration <= 0):
        raise ValueError("Длительность должна быть целым числом часов больше нуля.")

    catalog = list(providers) if providers is not None else load_providers()
    candidates = [p for p in catalog if _key(p.get("city")) == city
                  and category in {_key(x) for x in _list_field(p.get("categories"))}]
    counts: Counter[str] = Counter()
    matches = []
    blocked: list[tuple[dict[str, Any], set[str]]] = []  # (provider, failed conditions)
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
            blocked.append((provider, {cause for cause, failed in rejected.items() if failed}))
            continue

        name = str(provider.get("anon_name") or provider.get("name") or "")
        synthetic = _as_bool(provider.get("synthetic"))
        price_imputed = _as_bool(provider.get("price_imputed"))
        city_imputed = _as_bool(provider.get("city_imputed"))
        excerpt = _profile_excerpt(str(provider.get("description") or ""))
        category_label = next(x for x in _list_field(provider.get("categories")) if _key(x) == category)
        facts = [
            _fact("category", f"Категория: {category_label}"),
            _fact("available", f"Дата {_display_date(event_date)} в календаре свободна"),
            _fact("format", f"Берёт {FORMAT_PLURAL.get(event_format, f'формат «{event_format}»')}"),
            _fact("budget", f"{'Оценочная цена' if price_imputed else 'Цена'} от {_money(price)} укладывается в бюджет {_money(budget)}"),
        ]
        if language and language in languages:
            facts.append(_fact("language", f"Работает на языке «{language}»"))
        if duration is not None and max_hours is not None:
            facts.append(_fact("duration", f"Работает на площадке до {max_hours} ч — ваши {duration} ч покрывает"))
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
    city_label, category_label = str(request.get("city")).strip(), str(request.get("category")).strip()
    where = f"категории «{category_label}» в городе {city_label}"
    day = _display_day(event_date)
    hints = _hints(blocked, event_date, event_format, budget, duration)
    total = len(candidates)
    of_total = f"{total} {_plural(total, 'подрядчика', 'подрядчиков', 'подрядчиков')}"
    if results:
        count = len(matches)
        verb = _plural(count, "Подходит", "Подходят", "Подходят")
        headline = f"{verb} {count} из {of_total} {where} на {day}."
        if count > 3:
            headline += " Показываем 3 лучших."
        elif total == count:
            headline += " Других подрядчиков этой категории в городе нет."
        status = "matched"
    elif candidates:
        if total == 1:
            headline = f"Единственный подрядчик {where} не подходит на {day}."
        else:
            headline = f"Ни один из {of_total} {where} не подходит на {day}."
        status = "no_match"
    else:
        elsewhere = Counter(str(p.get("city")) for p in catalog
                            if category in {_key(x) for x in _list_field(p.get("categories"))})
        headline = f"В городе {city_label} нет подрядчиков категории «{category_label}»."
        if elsewhere:
            listed = ", ".join(f"{c} ({n} {_plural(n, 'подрядчик', 'подрядчика', 'подрядчиков')})"
                               for c, n in sorted(elsewhere.items()))
            hints = [f"Эта категория есть в другом городе: {listed}. Выберите его, если мероприятие можно провести там."
                     if len(elsewhere) == 1 else
                     f"Эта категория есть в других городах: {listed}."]
        else:
            hints = ["Такой категории нет в каталоге ни в одном городе."]
        status = "category_not_found"
    message = " ".join([headline, *hints])
    return {"status": status, "results": results, "message": message, "headline": headline,
            "hints": hints, "counts": summary}
