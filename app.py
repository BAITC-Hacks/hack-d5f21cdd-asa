"""Web UI. Run: python -m streamlit run app.py

The page only displays what service.run() returns; it never re-filters or re-ranks.
"""

from __future__ import annotations

import os
from datetime import date

import streamlit as st

from demo_scenarios import SCENARIOS
from matching import MAX_DATE, MIN_DATE, _money, load_providers
from service import run

FORMATS = ["свадьба", "той", "корпоратив", "конференция", "юбилей", "день рождения"]
LANGUAGES = ["не важно", "русский", "казахский", "английский"]
OUTCOME = {
    "matched": ("Подобрали", "success"),
    "category_not_found": ("В этом городе такой категории нет", "error"),
    "no_match": ("Кандидаты есть, но ни один не проходит по условиям", "warning"),
}
FACT_TITLES = {
    "compare": "Отличие от других карточек", "profile": "Из описания профиля",
    "format": "Формат", "available": "Календарь", "budget": "Бюджет", "category": "Категория",
    "language": "Язык", "duration": "Длительность", "synthetic": "Синтетика",
    "city_imputed": "Город",
}


@st.cache_data
def catalog_options() -> tuple[list[str], list[str]]:
    rows = load_providers()
    cities = sorted({r["city"] for r in rows})
    categories = sorted({c for r in rows for c in r["categories"]})
    return cities, categories


def simulated_llm_failure(_candidate):
    raise TimeoutError("simulated LLM outage")


def sidebar_request() -> tuple[dict | None, bool, bool]:
    cities, categories = catalog_options()
    st.sidebar.header("Параметры заказа")
    names = ["— свой запрос —", *SCENARIOS]
    preset_name = st.sidebar.selectbox("Готовый демо-сценарий", names)
    preset = SCENARIOS.get(preset_name, {}).get("request", {})
    key = f"form-{names.index(preset_name)}"  # new key -> widgets reset to the preset values

    with st.sidebar.form(key):
        city = st.selectbox("Город", cities, index=cities.index(preset.get("city", "Алматы")))
        event_date = st.date_input("Дата мероприятия", value=date.fromisoformat(preset.get("date", "2026-10-15")),
                                   min_value=MIN_DATE, max_value=MAX_DATE, format="DD.MM.YYYY")
        event_format = st.selectbox("Тип мероприятия", FORMATS,
                                    index=FORMATS.index(preset.get("event_format", "свадьба")))
        category = st.selectbox("Категория подрядчика", categories,
                                index=categories.index(preset.get("category", "Ведущий")))
        budget = st.number_input("Бюджет, ₸", min_value=0, step=50_000,
                                 value=int(preset.get("budget_kzt", 1_000_000)))
        st.caption("Опционально")
        duration = st.number_input("Длительность, ч (0 — не важно)", min_value=0, max_value=24,
                                   value=int(preset.get("duration_hours") or 0))
        language = st.selectbox("Язык", LANGUAGES, index=LANGUAGES.index(preset.get("language") or "не важно"))
        submitted = st.form_submit_button("Подобрать", type="primary", use_container_width=True)

    st.sidebar.divider()
    fail_llm = st.sidebar.toggle("Имитировать сбой LLM", help="Проверка: объяснения не пропадают, "
                                 "карточки получают reason_source = fallback.")
    debug = st.sidebar.toggle("Показать кухню (для жюри)", help="Баллы, факты, счётчики отказов.")
    st.sidebar.caption("LLM: " + ("OPENAI_API_KEY задан" if os.getenv("OPENAI_API_KEY") else
                                  "ключ не задан — работает детерминированный fallback"))

    if not (submitted or preset_name in SCENARIOS):
        return None, fail_llm, debug
    request = {"city": city, "date": event_date.isoformat(), "event_format": event_format,
               "category": category, "budget_kzt": int(budget)}
    if duration:
        request["duration_hours"] = int(duration)
    if language != "не важно":
        request["language"] = language
    return request, fail_llm, debug


def render_card(card: dict, debug: bool) -> None:
    with st.container(border=True):
        st.markdown(f"#### {card['name']}")
        st.caption(f"{' · '.join(card['categories'])} · {card['city']} · {card['id']}")
        price = f"от {_money(card['price_from_kzt'])}"
        st.markdown(f"**{price}**" + (" *(оценочная)*" if card["price_imputed"] else ""))

        badges = []
        if card["synthetic"]:
            badges.append(":green-background[синтетический профиль]")
        if card["price_imputed"]:
            badges.append(":orange-background[цена проставлена при подготовке]")
        if card["city_imputed"]:
            badges.append(":orange-background[город проставлен при подготовке]")
        if badges:
            st.markdown(" ".join(badges))

        st.write(card["reason"])
        source = "LLM выбрал факты" if card["reason_source"] == "llm" else "fallback-шаблон"
        st.caption(f"Объяснение: {source}")

        if debug:
            with st.expander(f"Баллы: {card['score']}"):
                st.json({"base": 70, **card["score_parts"]})
            with st.expander("Подтверждённые факты"):
                used = set(card["evidence_ids"])
                for fact in card["facts"]:
                    mark = "✅" if fact["id"] in used else "▫️"
                    st.markdown(f"{mark} **{FACT_TITLES.get(fact['id'], fact['id'])}:** {fact['text']}")


def main() -> None:
    st.set_page_config(page_title="Подбор подрядчиков", page_icon="🎉", layout="wide")
    st.title("Подбор event-подрядчиков")
    st.caption("До трёх подрядчиков из каталога и конкретное объяснение, почему каждый из них здесь. "
               "Занятые на дату, дороже бюджета и не берущие формат в выдачу не попадают.")

    request, fail_llm, debug = sidebar_request()
    if request is None:
        st.info("Выберите готовый демо-сценарий или заполните параметры слева и нажмите «Подобрать».")
        return

    try:
        with st.spinner("Подбираем…"):
            response = run(request, simulated_llm_failure if fail_llm else None)
    except ValueError as exc:
        st.error(f"Запрос некорректен: {exc}")
        return

    title, kind = OUTCOME[response["status"]]
    getattr(st, kind)(f"**{title}.** {response['message']}")

    results = response["results"]
    if results:
        for column, card in zip(st.columns(3), results):
            with column:
                render_card(card, debug)

    if debug:
        st.divider()
        left, right = st.columns(2)
        with left:
            st.markdown("**Запрос**")
            st.json(request)
        with right:
            st.markdown("**Счётчики отказов** (один профиль может попасть в несколько)")
            st.json(response["counts"])
        st.caption(f"Время ответа: {response['elapsed_ms']} мс")


main()
