"""All demo scenarios in the terminal, plus an LLM-outage check. Run: python demo.py"""

import sys

from demo_scenarios import SCENARIOS
from matching import _money
from service import run


def llm_outage(_candidate):
    raise TimeoutError("simulated LLM outage")


def show(title: str, request: dict, selector=None) -> None:
    response = run(request, selector)
    print(f"\n=== {title}")
    print(f"    запрос: {request}")
    print(f"    [{response['status']}, {response['elapsed_ms']} мс] {response['headline']}")
    for hint in response["hints"]:
        print(f"    → {hint}")
    for card in response["results"]:
        flags = [label for flag, label in (("synthetic", "синтетика"), ("price_imputed", "цена проставлена"),
                                           ("city_imputed", "город проставлен")) if card[flag]]
        extra = f" [{', '.join(flags)}]" if flags else ""
        print(f"  • {card['name']} — от {_money(card['price_from_kzt'])}, score {card['score']}, "
              f"{card['reason_source']}{extra}\n    {card['reason']}")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    for title, scenario in SCENARIOS.items():
        show(title, scenario["request"])
    first = next(iter(SCENARIOS.values()))["request"]
    show("10. Сбой LLM: тот же запрос, объяснения не пропадают (fallback)", first, llm_outage)


if __name__ == "__main__":
    main()
