"""Fact-grounded explanations for matches; optional OpenAI evidence selection."""

from __future__ import annotations

import json
import os
from typing import Any, Callable
from urllib.request import Request, urlopen


CORE_IDS = ("format", "available", "budget")
OPTIONAL_IDS = ("compare", "profile", "language", "duration", "synthetic", "city_imputed")
Selector = Callable[[dict[str, Any]], list[str]]


def _facts(candidate: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for fact in candidate.get("facts") or []:
        if isinstance(fact, dict) and isinstance(fact.get("id"), str) and isinstance(fact.get("text"), str):
            if fact["text"].strip() and fact["id"] not in result:
                result[fact["id"]] = fact["text"].strip()
    return result


def _fallback_ids(facts: dict[str, str]) -> list[str]:
    ids = [fact_id for fact_id in CORE_IDS if fact_id in facts]
    ids += [fact_id for fact_id in ("compare", "language", "duration", "profile", "synthetic", "city_imputed") if fact_id in facts]
    return ids


def _valid_ids(ids: Any, facts: dict[str, str], candidate: dict[str, Any]) -> bool:
    if not isinstance(ids, list) or not ids or not all(isinstance(x, str) for x in ids):
        return False
    if len(ids) != len(set(ids)) or any(x not in facts or x not in CORE_IDS + OPTIONAL_IDS for x in ids):
        return False
    if not set(CORE_IDS).issubset(ids):
        return False
    if "profile" in facts and "profile" not in ids:
        return False
    if "compare" in facts and "compare" not in ids:
        return False
    if candidate.get("synthetic") and "synthetic" not in ids:
        return False
    if candidate.get("city_imputed") and "city_imputed" not in ids:
        return False
    return True


def _lower(text: str) -> str:
    return text[0].lower() + text[1:] if text else text


def _upper(text: str) -> str:
    return text[0].upper() + text[1:] if text else text


def _render(ids: list[str], facts: dict[str, str], candidate: dict[str, Any]) -> str:
    # Only these exact verified facts enter the final text. Model prose is never
    # trusted as evidence, so dates, prices and claims cannot be hallucinated.
    core = "; ".join(_lower(facts[x]) for x in CORE_IDS)
    extras = [facts[x] for x in ("language", "duration", "city_imputed") if x in ids]
    if extras:
        core += "; " + "; ".join(_lower(x) for x in extras)
    core = core.rstrip(".!?")
    profile = facts["profile"].rstrip(".!?") if "profile" in ids and facts["profile"] not in facts.get("compare", "") else ""

    if "compare" in ids:
        # What sets this card apart goes first; shared checks go second. Still 2 sentences.
        first = facts["compare"].rstrip(".!?") + (f"; {_lower(profile)}" if profile else "")
        sentences = [first, core]
    else:
        sentences = [core] + ([profile] if profile else [])
    text = " ".join(_upper(x) + "." for x in sentences)
    if candidate.get("synthetic"):
        text = "Синтетический профиль для демонстрации: " + _lower(text)
    return text


def openai_select_evidence(candidate: dict[str, Any]) -> list[str]:
    """Ask one Responses API model which supplied facts to emphasize.

    The model sees only one candidate's approved facts, never the full catalog.
    An unset key disables the network path entirely.
    """
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    facts = _facts(candidate)
    payload = {
        "model": os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        "store": False,
        "input": [
            {"role": "developer", "content": (
                "Choose evidence IDs for a short Russian contractor recommendation. "
                "The mandatory IDs are format, available, budget, and compare and profile when present. "
                "Also include synthetic and city_imputed when present. "
                "Optionally include language and duration when useful. "
                "Use only supplied IDs. Return JSON; no unsupported claims."
            )},
            {"role": "user", "content": json.dumps({"facts": facts}, ensure_ascii=False)},
        ],
        "text": {"format": {
            "type": "json_schema", "name": "selected_evidence", "strict": True,
            "schema": {
                "type": "object", "properties": {
                    "evidence_ids": {"type": "array", "items": {"type": "string", "enum": list(facts)}}
                }, "required": ["evidence_ids"], "additionalProperties": False,
            },
        }},
        "max_output_tokens": 100,
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request("https://api.openai.com/v1/responses", data=body, headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json",
    })
    with urlopen(request, timeout=2.5) as response:
        data = json.load(response)
    if data.get("status") != "completed":
        raise ValueError("model response was not completed")
    output_text = next(
        (part.get("text") for item in data.get("output", []) if item.get("type") == "message"
         for part in item.get("content", []) if part.get("type") == "output_text"), None,
    )
    parsed = json.loads(output_text)
    return parsed["evidence_ids"]


def generate_reason(
    candidate: dict[str, Any], selector: Selector | None = None, *, use_llm: bool = True,
) -> dict[str, Any]:
    """Return reason, source and IDs. Any API/validation failure uses fallback."""
    facts = _facts(candidate)
    ids = _fallback_ids(facts)
    source = "fallback"
    if use_llm and set(CORE_IDS).issubset(facts) and (selector is not None or os.getenv("OPENAI_API_KEY")):
        try:
            selected = (selector or openai_select_evidence)(candidate)
            if _valid_ids(selected, facts, candidate):
                ids, source = selected, "llm"
        except Exception:
            pass  # Search must remain usable on timeout, API error or invalid JSON.
    if not set(CORE_IDS).issubset(facts):
        return {"reason": "Для этого профиля недостаточно подтверждённых фактов для объяснения.",
                "reason_source": "fallback", "evidence_ids": []}
    return {"reason": _render(ids, facts, candidate), "reason_source": source, "evidence_ids": ids}


def generateReason(candidateFacts: dict[str, Any]) -> dict[str, Any]:
    """Compatibility name from the team's initial contract."""
    return generate_reason(candidateFacts)


def explain_response(
    response: dict[str, Any], selector: Selector | None = None, *, use_llm: bool = True,
) -> dict[str, Any]:
    """Add explanations without changing selection, order, scores or statuses."""
    return {**response, "results": [
        {**candidate, **generate_reason(candidate, selector, use_llm=use_llm)}
        for candidate in response.get("results", [])
    ]}
