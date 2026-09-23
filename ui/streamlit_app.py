"""[Участник 2] Интерфейс. Запуск: streamlit run ui/streamlit_app.py

Вызывает recommend() напрямую (без HTTP), чтобы демо запускалось одной командой.
"""
import sys
from datetime import date
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import USE_LLM  # noqa: E402
from app.matching.filters import fmt_kzt  # noqa: E402
from app.models import (CALENDAR_END, CALENDAR_START, CATEGORIES, CITIES, EVENT_FORMATS,  # noqa: E402
                        LANGUAGES, RecommendRequest, Status)
from app.recommender import recommend  # noqa: E402

st.set_page_config(page_title="Подбор подрядчиков", page_icon="🎉", layout="wide")
st.title("Подбор подрядчиков")
st.caption("До 3 подрядчиков из каталога и объяснение, почему именно они. "
           f"Объяснения: {'LLM + проверка фактов' if USE_LLM else 'шаблоны из фактов (LLM выключен)'}.")

with st.sidebar.form("query"):
    city = st.selectbox("Город", CITIES)
    event_date = st.date_input("Дата", value=date(2026, 10, 15),
                               min_value=CALENDAR_START, max_value=CALENDAR_END)
    event_format = st.selectbox("Тип мероприятия", EVENT_FORMATS)
    category = st.selectbox("Категория", CATEGORIES, index=CATEGORIES.index("Ведущий"))
    budget = st.number_input("Бюджет, ₸", min_value=10_000, value=1_000_000, step=50_000)
    st.markdown("**Опционально**")
    duration = st.number_input("Длительность, ч (0 — не важно)", min_value=0, max_value=24, value=0)
    language = st.selectbox("Язык", ["не важно", *LANGUAGES])
    show_debug = st.checkbox("Показать score и отсеянных (для жюри)")
    submitted = st.form_submit_button("Подобрать", type="primary")

if not submitted:
    st.info("Заполните параметры слева и нажмите «Подобрать».")
    st.stop()

req = RecommendRequest(
    city=city, date=event_date, event_format=event_format, category=category,
    budget_kzt=int(budget), duration_hours=int(duration) or None,
    language=None if language == "не важно" else language,
)
with st.spinner("Подбираем…"):
    resp = recommend(req)

if resp.status == Status.MATCHED:
    st.success(resp.message)
    if resp.shortage_reason:
        st.warning(f"Показано меньше трёх. {resp.shortage_reason}")
elif resp.status == Status.CATEGORY_NOT_FOUND:
    st.error("**В этом городе такой категории нет.** " + resp.message)
else:
    st.warning("**Кандидаты есть, но никто не проходит по условиям.** " + resp.message)

cols = st.columns(3)
for col, card in zip(cols, resp.results):
    with col, st.container(border=True):
        badges = []
        if card.synthetic:
            badges.append(":green-background[синтетический профиль]")
        if card.price_imputed:
            badges.append(":orange-background[цена ориентировочная]")
        if card.city_imputed:
            badges.append(":orange-background[город проставлен]")
        st.markdown(f"### {card.name}")
        st.caption(f"{' · '.join(card.categories)} · {card.city} · {card.id}")
        st.markdown(f"**от {fmt_kzt(card.price_from_kzt)}**")
        if badges:
            st.markdown(" ".join(badges))
        st.write(card.explanation)
        if show_debug:
            st.caption(f"score {card.score} · источник: {card.explanation_source}")
            st.json(card.score_parts, expanded=False)

if show_debug and resp.rejections:
    with st.expander(f"Отсеяны ({len(resp.rejections)})"):
        for r in resp.rejections:
            st.markdown(f"- **{r.vendor_name}** ({r.vendor_id}): {r.detail}")
if show_debug:
    st.caption(f"Время ответа: {resp.elapsed_ms} мс")
