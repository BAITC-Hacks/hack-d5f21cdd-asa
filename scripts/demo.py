"""[Участник 3] Демо-сценарии из DoD в терминале. Запуск: python -m scripts.demo

TODO(У3): подобрать финальные запросы по реальным данным и зафиксировать ожидаемую выдачу в README.
"""
import sys
from datetime import date

from app.matching.filters import fmt_kzt
from app.models import RecommendRequest
from app.recommender import recommend

SCENARIOS = [
    ("1. Плотная категория, осень",
     dict(city="Алматы", date=date(2026, 10, 15), event_format="свадьба", category="Ведущий", budget_kzt=1_000_000)),
    ("2. Тот же запрос в декабре — занятость меняет выдачу",
     dict(city="Алматы", date=date(2026, 12, 19), event_format="свадьба", category="Ведущий", budget_kzt=1_000_000)),
    ("3. Площадка — тот же механизм",
     dict(city="Алматы", date=date(2026, 11, 14), event_format="свадьба", category="Банкетный зал", budget_kzt=3_500_000)),
    ("4. Редкая категория",
     dict(city="Алматы", date=date(2026, 12, 5), event_format="свадьба", category="Декоратор", budget_kzt=3_000_000)),
    ("5. Кандидаты есть, но никто не подходит",
     dict(city="Астана", date=date(2026, 10, 14), event_format="той", category="Флорист", budget_kzt=400_000)),
    ("6. Категории нет в городе",
     dict(city="Астана", date=date(2026, 10, 14), event_format="той", category="Лайв-бэнд", budget_kzt=2_000_000)),
]


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    for title, q in SCENARIOS:
        r = recommend(RecommendRequest(**q))
        print(f"\n=== {title}\n{q}\n[{r.status.value}, {r.elapsed_ms} мс] {r.message}")
        if r.shortage_reason:
            print(f"  Меньше трёх: {r.shortage_reason}")
        for c in r.results:
            tag = " [synthetic]" if c.synthetic else ""
            print(f"  • {c.name}{tag} — от {fmt_kzt(c.price_from_kzt)} ({c.explanation_source})\n    {c.explanation}")


if __name__ == "__main__":
    main()
