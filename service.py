"""Single entry point used by the UI, the demo script and tests.

request -> matching.recommend  (filters, ranking, top-3, verified facts, counts)
        -> comparison          (one LLM call for grounded pros and cons, or fallback)
        -> explanations        (deterministic reason for compatibility)
"""

from __future__ import annotations

import time
from typing import Any

from comparison import ComparisonSelector, add_comparative_facts
from explanations import Selector, explain_response
from matching import recommend


def run(
    request: dict[str, Any], selector: Selector | None = None,
    comparison_selector: ComparisonSelector | None = None,
) -> dict[str, Any]:
    """Full pipeline. Raises ValueError for an invalid request (see matching.recommend)."""
    started = time.perf_counter()
    response = recommend(request)
    response = add_comparative_facts(response, request, comparison_selector or selector)
    response = explain_response(response, selector, use_llm=selector is not None)
    return {**response, "elapsed_ms": round((time.perf_counter() - started) * 1000)}
