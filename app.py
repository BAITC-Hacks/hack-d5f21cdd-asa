"""Streamlit UI for the contractor shortlist. Run: python -m streamlit run app.py."""

from __future__ import annotations

import os
import re
from datetime import date
from html import escape
from pathlib import Path

import streamlit as st

from demo_scenarios import SCENARIOS
from matching import MAX_DATE, MIN_DATE, _money, load_providers
from service import run

FORMATS = ["свадьба", "той", "корпоратив", "конференция", "юбилей", "день рождения"]
LANGUAGES = ["не важно", "русский", "казахский", "английский"]
OUTCOME = {
    "matched": "Подходящие подрядчики",
    "category_not_found": "В этом городе нет такой категории",
    "no_match": "Пока нет подходящих вариантов",
}
SCORE_TITLES = {
    "specialization": "Позиция формата в списке каталога",
    "description": "Описание говорит об этом виде событий",
    "language": "Совпал выбранный язык",
    "duration": "Длительность подтверждена данными",
}
COUNT_TITLES = {
    "category_candidates": "Всего в категории в этом городе",
    "busy": "Заняты на дату",
    "over_budget": "Дороже бюджета",
    "format": "Не берут этот тип мероприятия",
    "duration": "Не работают столько часов",
    "price_missing": "Без указанной цены",
}
FACT_TITLES = {
    "compare": "Отличие от других карточек", "profile": "Из описания профиля",
    "format": "Формат", "available": "Календарь", "budget": "Бюджет",
    "category": "Категория", "language": "Язык", "duration": "Длительность",
    "synthetic": "Синтетика", "city_imputed": "Город",
}


@st.cache_data
def catalog_options() -> tuple[list[str], list[str]]:
    rows = load_providers()
    cities = sorted({row["city"] for row in rows})
    categories = sorted({category for row in rows for category in row["categories"]})
    return cities, categories


def keep_numbers_together(text: str) -> str:
    """«1 000 000 ₸» must not wrap across lines: use non-breaking spaces inside amounts."""
    return re.sub(r"(?<=\d) (?=\d{3}(?!\d))|(?<=\d) (?=₸)", " ", text)


def simulated_llm_failure(_candidate):
    raise TimeoutError("simulated LLM outage")


def search_request() -> tuple[dict | None, bool, bool]:
    cities, categories = catalog_options()
    names = ["Свой запрос", *SCENARIOS]
    # ?scenario=2 opens a demo request directly for a presentation.
    scenario_param = st.query_params.get("scenario", "0")
    initial_scenario = int(scenario_param) if scenario_param.isdigit() else 0
    initial_scenario = min(initial_scenario, len(names) - 1)
    st.markdown(
        '<div class="search-intro"><span class="section-index">01 / ПАРАМЕТРЫ</span>'
        '<h2>Ваше событие</h2></div>',
        unsafe_allow_html=True,
    )
    preset_name = st.selectbox(
        "Быстрый сценарий", names, index=initial_scenario,
        help="Выберите пример и при желании измените поля."
    )
    preset = SCENARIOS.get(preset_name, {}).get("request", {})
    key = f"form-{names.index(preset_name)}"

    with st.form(key):
        city = st.selectbox("Город", cities, index=cities.index(preset.get("city", "Алматы")))
        event_date = st.date_input(
            "Дата", value=date.fromisoformat(preset.get("date", "2026-10-15")),
            min_value=MIN_DATE, max_value=MAX_DATE, format="DD.MM.YYYY",
        )
        category = st.selectbox(
            "Категория подрядчика", categories,
            index=categories.index(preset.get("category", "Ведущий")),
        )
        event_format = st.selectbox(
            "Формат", FORMATS,
            index=FORMATS.index(preset.get("event_format", "свадьба")),
        )
        budget = st.number_input(
            "Бюджет, ₸", min_value=0, step=50_000,
            value=int(preset.get("budget_kzt", 1_000_000)),
        )
        duration = st.number_input(
            "Часы · 0 = любые", min_value=0, max_value=24,
            value=int(preset.get("duration_hours") or 0),
        )
        language = st.selectbox(
            "Язык", LANGUAGES,
            index=LANGUAGES.index(preset.get("language") or "не важно"),
        )
        submitted = st.form_submit_button(
            "Найти TOP‑3  →", type="primary", use_container_width=True,
        )

    with st.expander("Настройки демонстрации"):
        fail_llm = st.toggle("Имитировать сбой ИИ", help="При сбое сравнение строится из данных каталога.")
        debug = st.toggle(
            "Подробности подбора",
            value=st.query_params.get("debug") == "1",
            help="Показать баллы, факты и причины отсева.",
        )
        st.caption(
            "ИИ сравнит варианты одним запросом." if os.getenv("OPENAI_API_KEY")
            else "Без API-ключа сравнение строится из данных каталога."
        )

    if not (submitted or preset_name in SCENARIOS):
        return None, fail_llm, debug
    request = {
        "city": city, "date": event_date.isoformat(), "event_format": event_format,
        "category": category, "budget_kzt": int(budget),
    }
    if duration:
        request["duration_hours"] = int(duration)
    if language != "не важно":
        request["language"] = language
    return request, fail_llm, debug


def _point_list(points: list[dict], kind: str, empty_text: str) -> str:
    items = "".join(
        f'<li title="{escape(point["text"], quote=True)}">{escape(point["text"])}</li>'
        for point in points
    )
    if not items:
        items = f'<li class="muted">{escape(empty_text)}</li>'
    return f'<ul class="point-list {kind}">{items}</ul>'


def render_card(card: dict, debug: bool, rank: int) -> None:
    comparison = card.get("comparison") or {"pros": [], "cons": []}
    source = {
        "llm": "ИИ сравнил варианты · по проверенным фактам",
        "fallback": "Сравнение по данным каталога",
        "catalog": "Единственный подходящий вариант",
    }.get(card.get("comparison_source"), "Сравнение по данным каталога")
    badges = []
    if card["synthetic"]:
        badges.append('<span class="provider-tag">демо-профиль</span>')
    if card["price_imputed"]:
        badges.append('<span class="provider-tag">оценочная цена</span>')
    if card["city_imputed"]:
        badges.append('<span class="provider-tag">город добавлен</span>')
    tags = f'<div class="provider-tags">{"".join(badges)}</div>' if badges else ""
    pros = _point_list(comparison["pros"], "pros", "Нет подтверждённых преимуществ по сравнению с другими.")
    cons = _point_list(comparison["cons"], "cons", "По указанным данным явных ограничений нет.")
    st.markdown(f"""
<article class="provider-card rank-{rank}">
  <div class="provider-main">
    <div class="provider-topline"><span class="provider-rank">{rank:02d}</span><span class="provider-id">{escape(card['id'])}</span></div>
    <div class="provider-name">{escape(card['name'])}</div>
    <div class="provider-meta">{escape(' · '.join(card['categories']))}<span class="meta-dot">◆</span>{escape(card['city'])}</div>
    <div class="provider-price"><span>СТОИМОСТЬ ОТ</span><strong>{escape(_money(card['price_from_kzt']))}</strong></div>
    {tags}
  </div>
  <div class="provider-comparison pros-column">
    <div class="provider-label pros-label"><span>+</span> Плюсы</div>{pros}
  </div>
  <div class="provider-comparison cons-column">
    <div class="provider-label cons-label"><span>−</span> Что учесть</div>{cons}
  </div>
  <div class="provider-reason">{escape(keep_numbers_together(card['reason']))}</div>
  <div class="provider-source">{escape(source)}</div>
</article>
""", unsafe_allow_html=True)

    if debug:
        with st.expander(f"Как получен балл {card['score']}"):
            lines = ["- База за прохождение условий: **70**"]
            lines += [f"- {SCORE_TITLES.get(k, k)}: **+{v}**" for k, v in card["score_parts"].items()]
            st.markdown("\n".join(lines))
        with st.expander("Проверенные факты"):
            used = set(card["evidence_ids"])
            for fact in card["facts"]:
                mark = "✓" if fact["id"] in used else "▫"
                st.markdown(f"{mark} **{FACT_TITLES.get(fact['id'], fact['id'])}:** {fact['text']}")


def main() -> None:
    st.set_page_config(
        page_title="ASA | подбор подрядчиков", page_icon="✦", layout="wide",
        initial_sidebar_state="collapsed",
    )
    css = Path(__file__).with_name("styles.css").read_text(encoding="utf-8")
    st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)
    st.markdown("""
<header class="site-header">
  <div class="brand-mark">A<span>✦</span></div>
  <div class="brand-name">ASA <span>/ EVENT MATCH</span></div>
  <div class="header-note">ПОДРЯДЧИКИ ДЛЯ ВАШЕГО СОБЫТИЯ</div>
</header>
<section class="asa-hero">
  <div><div class="hero-eyebrow"><span></span> ПОДБОР С ОПОРОЙ НА ФАКТЫ</div>
    <h1>Найдите своих. <em>Увидьте разницу.</em></h1>
  </div>
  <p>Дата, формат и бюджет проверены. Сравните до трёх подходящих подрядчиков.</p>
</section>
""", unsafe_allow_html=True)

    search_col, results_col = st.columns([.85, 2.15], gap="large")
    with search_col:
        request, fail_llm, debug = search_request()

    with results_col:
        if request is None:
            st.markdown(
                '<div class="empty-start"><span>✦</span><div><strong>Сначала задайте параметры</strong>'
                '<p>TOP‑3 появится здесь после нажатия «Найти».</p></div></div>',
                unsafe_allow_html=True,
            )
            return

        try:
            with st.spinner("Подбираем варианты…"):
                response = run(request, simulated_llm_failure if fail_llm else None)
        except ValueError as exc:
            st.error(f"Проверьте параметры: {exc}")
            return

        results, hints = response["results"], response["hints"]
        status_class = {"matched": "matched", "no_match": "empty", "category_not_found": "missing"}[
            response["status"]
        ]
        st.markdown(
            f'<section class="result-summary {status_class}"><div class="result-count">{len(results):02d}'
            f'<span> / 03</span></div><div><div class="result-eyebrow">02 / РЕЗУЛЬТАТ</div>'
            f'<h2>{escape(OUTCOME[response["status"]])}</h2>'
            f'<p>{escape(response["headline"])}</p></div></section>',
            unsafe_allow_html=True,
        )

        if hints:
            advice = "\n".join(f"- {keep_numbers_together(hint)}" for hint in hints)
            if len(results) == 3:
                with st.expander("Почему не подошли остальные и как расширить выбор"):
                    st.markdown(advice)
            else:
                st.markdown('<div class="advice-title">Что можно изменить в запросе</div>', unsafe_allow_html=True)
                st.markdown(advice)

        if results:
            st.markdown(
                '<div class="section-heading"><div><span class="section-index">03 / СРАВНЕНИЕ</span>'
                '<h2>Короткий список</h2></div><p>Плюсы и ограничения по данным профилей.</p></div>',
                unsafe_allow_html=True,
            )
            for rank, card in enumerate(results, start=1):
                render_card(card, debug, rank)

        if debug:
            with st.expander("Технические подробности"):
                st.markdown("**Запрос**")
                st.json(request)
                st.markdown("**Отсеяно по причинам**")
                st.markdown("\n".join(
                    f"- {COUNT_TITLES.get(k, k)}: **{v}**"
                    for k, v in response["counts"].items()
                ))
                st.caption(f"Время ответа: {response['elapsed_ms']} мс")


main()
