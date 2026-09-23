"""[Участник 2] Объяснения через Claude API.

LLM не выбирает и не ранжирует — только формулирует уже проверенные факты.
Один вызов на всю выдачу: модель видит все карточки и пишет, чем каждая отличается.
Кэш по хэшу промпта: тот же запрос -> тот же текст (детерминизм) и мгновенный ответ.

Включение: USE_LLM=1 и ANTHROPIC_API_KEY в окружении (см. .env.example).
Любая ошибка -> None -> recommender берёт шаблон.

TODO(У2): тюнинг промпта на демо-запросах; сравнить скорость моделей (LLM_MODEL).
"""
from __future__ import annotations

import hashlib
import json
import logging

from app.config import CACHE_DIR, LLM_MODEL, LLM_TIMEOUT_S
from app.matching.filters import fmt_date, fmt_kzt
from app.models import Candidate, RecommendRequest

log = logging.getLogger(__name__)

SYSTEM = """Ты пишешь объяснения к рекомендациям event-подрядчиков для заказчика в Казахстане.
Для каждого подрядчика напиши 1–2 предложения на русском: почему он в подборке и чем он
отличается от остальных карточек этой же подборки.

Правила:
- Используй ТОЛЬКО переданные факты и описание. Не придумывай цифры, награды, опыт.
- Начинай с самого отличающего факта, а не с общего.
- Никаких общих фраз («отличный выбор», «идеально подойдёт для вашего мероприятия»).
- Не повторяй одну и ту же конструкцию предложения для разных подрядчиков.
- Не упоминай score и внутренние баллы."""

SCHEMA = {
    "type": "object",
    "properties": {
        "explanations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"id": {"type": "string"}, "text": {"type": "string"}},
                "required": ["id", "text"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["explanations"],
    "additionalProperties": False,
}


def build_prompt(shown: list[Candidate], req: RecommendRequest) -> str:
    lines = [
        f"Запрос: {req.category}, {req.city}, {fmt_date(req.date)}, формат «{req.event_format}», "
        f"бюджет {fmt_kzt(req.budget_kzt)}"
        + (f", язык {req.language}" if req.language else "")
        + (f", длительность {req.duration_hours} ч" if req.duration_hours else ""),
        "",
    ]
    for c in shown:
        v = c.vendor
        lines += [
            f"id: {v.id}",
            f"имя: {v.name}",
            "факты: " + "; ".join(c.facts),
            f"описание: {v.description[:600]}",
            "",
        ]
    return "\n".join(lines)


def _cache_path(prompt: str):
    key = hashlib.sha256(f"{LLM_MODEL}\n{SYSTEM}\n{prompt}".encode()).hexdigest()[:24]
    return CACHE_DIR / f"llm_{key}.json"


def llm_explanations(shown: list[Candidate], req: RecommendRequest) -> dict[str, str] | None:
    prompt = build_prompt(shown, req)
    path = _cache_path(prompt)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))

    try:
        import anthropic

        client = anthropic.Anthropic(timeout=LLM_TIMEOUT_S, max_retries=1)
        response = client.messages.create(
            model=LLM_MODEL,
            max_tokens=4000,
            system=SYSTEM,
            messages=[{"role": "user", "content": prompt}],
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
        )
        if response.stop_reason != "end_turn":
            log.warning("LLM stop_reason=%s", response.stop_reason)
            return None
        text = next(b.text for b in response.content if b.type == "text")
        result = {e["id"]: e["text"].strip() for e in json.loads(text)["explanations"]}
    except Exception as e:  # noqa: BLE001 — любой сбой LLM не должен ронять подбор
        log.warning("LLM недоступен, используем шаблон: %s", e)
        return None

    CACHE_DIR.mkdir(exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return result
