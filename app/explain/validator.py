"""[Участник 2] Проверка объяснений.

Отклоняем текст, если:
- есть общие фразы без фактов;
- в тексте есть число, которого нет ни в фактах, ни в профиле (галлюцинация);
- два объяснения одной выдачи слишком похожи (DoD: карточки нельзя перепутать).
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher

from app.models import Candidate

BANNED = [
    "отличный выбор", "идеально подойдёт", "идеально подойдет", "прекрасный вариант",
    "лучший выбор", "для вашего мероприятия", "не пожалеете", "высокий профессионализм",
]
SIMILARITY_LIMIT = 0.75


def _numbers(text: str) -> set[str]:
    return {n.replace(" ", "") for n in re.findall(r"\d[\d\s]*\d|\d", text)}


def is_valid(text: str, c: Candidate, request_text: str = "") -> tuple[bool, str]:
    """request_text — строка запроса (дата, бюджет): её числа тоже разрешены."""
    low = text.lower()
    if not text.strip():
        return False, "пусто"
    if any(b in low for b in BANNED):
        return False, "общая фраза"
    source = " ".join([*c.facts, c.vendor.description, request_text,
                       str(c.vendor.price_from_kzt), str(c.vendor.max_hours)])
    unknown = _numbers(text) - _numbers(source)
    if unknown:
        return False, f"числа не из фактов: {sorted(unknown)}"
    return True, "ok"


def too_similar(a: str, b: str) -> bool:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio() > SIMILARITY_LIMIT
