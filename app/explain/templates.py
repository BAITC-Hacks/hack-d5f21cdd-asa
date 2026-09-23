"""[Участник 2] Шаблонное объяснение — fallback, если LLM выключен/упал/не прошёл валидацию.

Берём 2 самых приоритетных факта + доступность на дату.
"""
from __future__ import annotations

from app.models import Candidate


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:] if s else s


def template_explanation(c: Candidate) -> str:
    facts = [f for f in c.facts if not f.startswith("свободен")]
    free = next((f for f in c.facts if f.startswith("свободен")), "")
    head = "; ".join(facts[:2]) if facts else "проходит все условия запроса"
    tail = f" {_cap(free)}." if free else ""
    return f"{_cap(head)}.{tail}"
