"""Behavior checks for matching and grounded explanations."""

import json
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from comparison import add_comparative_facts, openai_compare
from explanations import explain_response, generate_reason
from matching import load_providers, recommend


BASE = {
    "city": "Алматы", "date": "2026-10-15", "event_format": "корпоратив",
    "category": "Ведущий", "budget_kzt": 500000,
}


def provider(provider_id, *, categories="Ведущий", busy_dates="", price=400000,
             event_formats="корпоратив", max_hours="6", languages="русский",
             description="Проводит корпоративные события в камерном формате.", **flags):
    return {
        "id": provider_id, "anon_name": provider_id, "city": "Алматы",
        "categories": categories, "busy_dates": busy_dates, "price_from_kzt": price,
        "event_formats": event_formats, "max_hours": max_hours,
        "languages": languages, "description": description, **flags,
    }


class MatchingTests(unittest.TestCase):
    def test_catalog_types(self):
        rows = load_providers()
        self.assertEqual(len(rows), 66)
        self.assertEqual(sum(x["synthetic"] for x in rows), 13)
        self.assertTrue(all(isinstance(x["busy_dates"], list) for x in rows))

    def test_hard_filters_and_rejection_counts(self):
        rows = [
            provider("A", categories="Фотограф|Ведущий", price=500000),
            provider("B", busy_dates="2026-10-15"),
            provider("C", price=500001),
            provider("D", event_formats="свадьба"),
            provider("E", max_hours="3"),
        ]
        result = recommend({**BASE, "duration_hours": 5}, rows)
        self.assertEqual([x["id"] for x in result["results"]], ["A"])
        self.assertEqual(result["counts"], {
            "category_candidates": 5, "busy": 1, "over_budget": 1,
            "format": 1, "duration": 1, "price_missing": 0,
        })
        self.assertIn("кроме даты", result["message"])
        self.assertIn("500 000", next(f["text"] for f in result["results"][0]["facts"] if f["id"] == "budget"))

    def test_three_outcomes_and_stable_order_without_cheapest_bonus(self):
        rows = [provider("B", price=100000), provider("A", price=500000)]
        first = recommend(BASE, rows)
        self.assertEqual(first["status"], "matched")
        self.assertEqual([x["id"] for x in first["results"]], ["A", "B"])
        self.assertEqual(first, recommend(BASE, rows))
        self.assertEqual(recommend({**BASE, "category": "Флорист"}, rows)["status"], "category_not_found")
        empty = recommend({**BASE, "budget_kzt": 0}, rows)
        self.assertEqual(empty["status"], "no_match")
        self.assertIn("кроме бюджета", empty["message"])

    def test_optional_duration_and_language(self):
        rows = [provider("A", max_hours="", languages="английский"), provider("B", max_hours="5")]
        result = recommend({**BASE, "duration_hours": 7, "language": "английский"}, rows)
        self.assertEqual([x["id"] for x in result["results"]], ["A"])
        self.assertIn("language", {f["id"] for f in result["results"][0]["facts"]})

    def test_real_calendar_changes_availability(self):
        row = next(x for x in load_providers() if x["id"] == "HK-39372")
        busy = recommend({**BASE, "category": "Флорист", "budget_kzt": 300000,
                          "event_format": "свадьба", "date": "2026-10-17"}, [row])
        free = recommend({**BASE, "category": "Флорист", "budget_kzt": 300000,
                          "event_format": "свадьба", "date": "2026-10-18"}, [row])
        self.assertEqual(busy["status"], "no_match")
        self.assertEqual(free["status"], "matched")
        self.assertIn("18 октября", explain_response(free)["results"][0]["reason"])

    def test_request_validation(self):
        for change in ({"date": "2027-01-01"}, {"budget_kzt": -1},
                       {"duration_hours": 0}, {"budget_kzt": "12.5"},
                       {"date": "20261015"}, {"date": "2026-W42-4"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                recommend({**BASE, **change}, [])

    def test_empty_csv_and_missing_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            empty = Path(directory) / "empty.csv"
            empty.write_text("", encoding="utf-8")
            self.assertEqual(load_providers(empty), [])
        result = recommend(BASE, [])
        self.assertEqual(result["status"], "category_not_found")
        self.assertEqual(result["results"], [])
        with self.assertRaises(ValueError):
            recommend({**BASE, "city": ""}, [])

    def test_pipe_fields_and_limit_of_three(self):
        rows = [provider(str(i), categories="Фотограф|Ведущий",
                         event_formats="свадьба|корпоратив", languages="казахский|русский",
                         busy_dates="2026-10-14|2026-10-16") for i in range(5)]
        result = recommend({**BASE, "language": "русский"}, rows)
        self.assertEqual([card["id"] for card in result["results"]], ["0", "1", "2"])
        self.assertEqual(result["status"], "matched")


class ExplanationTests(unittest.TestCase):
    def setUp(self):
        self.candidate = recommend(BASE, [provider("A", price_imputed=True, synthetic=True)])["results"][0]

    def test_fallback_and_provenance(self):
        result = generate_reason(self.candidate)
        self.assertEqual(result["reason_source"], "fallback")
        self.assertIn("Синтетический", result["reason"])
        self.assertIn("оценочная цена от", result["reason"])
        self.assertIn("synthetic", result["evidence_ids"])
        self.assertEqual(len(result["reason"].split(". ")), 2)

    def test_valid_model_selection_and_invalid_fallback(self):
        ids = [x["id"] for x in self.candidate["facts"] if x["id"] in
               {"format", "available", "budget", "profile", "synthetic"}]
        self.assertEqual(generate_reason(self.candidate, lambda _: ids)["reason_source"], "llm")
        for selector in (lambda _: ["format", "available", "budget", "fabricated"],
                         lambda _: ["format", "available", "budget"],
                         lambda _: (_ for _ in ()).throw(TimeoutError())):
            with self.subTest(selector=selector):
                self.assertEqual(generate_reason(self.candidate, selector)["reason_source"], "fallback")

    def test_explain_response_preserves_order(self):
        raw = recommend(BASE, [provider("B"), provider("A")])
        out = explain_response(raw)
        self.assertEqual([x["id"] for x in out["results"]], ["A", "B"])
        self.assertTrue(all(x["reason"] for x in out["results"]))


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.raw = recommend(BASE, [
            provider("A", price=100000, price_imputed=True, languages="русский|английский"),
            provider("B", price=200000, max_hours="8"),
            provider("C", price=300000, synthetic=True),
        ])

    def test_model_sees_all_cards_and_only_verified_points_are_shown(self):
        seen = []

        def choose(payload):
            seen.append(payload)
            return [{"id": card["id"], "pros": [card["pros"][0]["id"]], "cons": []}
                    for card in payload["cards"]]

        result = add_comparative_facts(self.raw, BASE, choose)
        self.assertEqual(len(seen), 1)
        self.assertEqual(len(seen[0]["cards"]), 3)
        self.assertEqual(seen[0]["request"]["budget_kzt"], BASE["budget_kzt"])
        self.assertEqual([card["id"] for card in result["results"]],
                         [card["id"] for card in self.raw["results"]])
        self.assertTrue(all(card["comparison_source"] == "llm" for card in result["results"]))
        self.assertIn("оценочная", " ".join(point["text"] for point in result["results"][0]["comparison"]["cons"]))
        self.assertIn("демонстрации", " ".join(point["text"] for point in result["results"][2]["comparison"]["cons"]))

    def test_invalid_model_ids_use_grounded_fallback(self):
        result = add_comparative_facts(self.raw, BASE, lambda _: [
            {"id": "A", "pros": ["fabricated"], "cons": []},
            {"id": "B", "pros": ["fabricated"], "cons": []},
            {"id": "C", "pros": ["fabricated"], "cons": []},
        ])
        self.assertTrue(all(card["comparison_source"] == "fallback" for card in result["results"]))
        self.assertNotIn("fabricated", str(result))

    def test_responses_api_payload_is_one_structured_request(self):
        choice = [{"id": "A", "pros": ["price_lowest"], "cons": []}]
        captured = []

        def fake_urlopen(request, timeout):
            captured.append(json.loads(request.data))
            response = {"status": "completed", "output": [{"type": "message", "content": [
                {"type": "output_text", "text": json.dumps({"cards": choice})}]}]}
            return BytesIO(json.dumps(response).encode())

        with patch.dict("os.environ", {"OPENAI_API_KEY": "fake-test-key"}), patch("comparison.urlopen", fake_urlopen):
            self.assertEqual(openai_compare({"cards": [{"id": "A"}]}), choice)
        self.assertEqual(len(captured), 1)
        self.assertFalse(captured[0]["store"])
        self.assertEqual(captured[0]["text"]["format"]["type"], "json_schema")


if __name__ == "__main__":
    unittest.main()
