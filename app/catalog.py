"""[Участник 1] Загрузка и нормализация каталога.

CSV -> list[Vendor]. Многозначные поля разделены '|'. Пустой max_hours -> None.
Все CSV из config.DATA_FILES объединяются (vendors.csv + synthetic_extra.csv).
"""
from __future__ import annotations

import csv
from datetime import date
from functools import lru_cache
from pathlib import Path

from app.config import DATA_FILES
from app.models import Vendor


def _split(value: str) -> list[str]:
    return [v.strip() for v in value.split("|") if v.strip()]


def _bool(value: str) -> bool:
    return value.strip().lower() in {"true", "1", "yes"}


def parse_row(row: dict[str, str]) -> Vendor:
    max_hours = row["max_hours"].strip()
    return Vendor(
        id=row["id"].strip(),
        name=row["anon_name"].strip(),
        categories=_split(row["categories"]),
        city=row["city"].strip(),
        city_imputed=_bool(row["city_imputed"]),
        synthetic=_bool(row["synthetic"]),
        price_from_kzt=int(float(row["price_from_kzt"])),
        price_imputed=_bool(row["price_imputed"]),
        event_formats=_split(row["event_formats"]),
        languages=_split(row["languages"]),
        max_hours=int(float(max_hours)) if max_hours else None,
        busy_dates=frozenset(date.fromisoformat(d) for d in _split(row["busy_dates"])),
        description=" ".join(row["description"].split()),
    )


def load_file(path: Path) -> list[Vendor]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as f:
        return [parse_row(row) for row in csv.DictReader(f) if row.get("id")]


@lru_cache(maxsize=1)
def load_catalog() -> tuple[Vendor, ...]:
    vendors: dict[str, Vendor] = {}
    for path in DATA_FILES:
        for v in load_file(path):
            if v.id in vendors:
                raise ValueError(f"Дубликат id {v.id} в {path.name}")
            vendors[v.id] = v
    # Сортировка по id — один источник детерминизма на всём пути
    return tuple(sorted(vendors.values(), key=lambda v: v.id))
