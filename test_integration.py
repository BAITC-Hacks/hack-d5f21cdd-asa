"""End-to-end checks of the Definition of Done on the real catalog. Run: python -m unittest"""

import re
import unittest
from unittest.mock import patch

from demo_scenarios import SCENARIOS
from matching import load_providers
from service import run

CATALOG = {row["id"]: row for row in load_providers()}
GENERIC_PHRASES = ("отличный выбор", "идеально подойдёт", "лучший выбор", "для вашего мероприятия")


def outage(_candidate):
    raise TimeoutError("simulated LLM outage")


class DefinitionOfDoneTests(unittest.TestCase):
    def test_demo_scenarios_give_expected_outcome(self):
        for title, scenario in SCENARIOS.items():
            with self.subTest(title):
                response = run(scenario["request"])
                self.assertEqual(response["status"], scenario["expect"])
                self.assertEqual(len(response["results"]), scenario["cards"])
                self.assertTrue(response["message"].strip())

    def test_hard_constraints_never_violated(self):
        for title, scenario in SCENARIOS.items():
            request = scenario["request"]
            for card in run(request)["results"]:
                with self.subTest(title, card=card["id"]):
                    row = CATALOG[card["id"]]
                    self.assertNotIn(request["date"], row["busy_dates"])
                    self.assertLessEqual(row["price_from_kzt"], request["budget_kzt"])
                    self.assertIn(request["event_format"], row["event_formats"])
                    self.assertIn(request["category"], row["categories"])
                    self.assertEqual(row["city"], request["city"])

    def test_same_request_same_order(self):
        for scenario in SCENARIOS.values():
            first = run(scenario["request"])
            second = run(scenario["request"])
            self.assertEqual([c["id"] for c in first["results"]], [c["id"] for c in second["results"]])
            self.assertEqual([c["reason"] for c in first["results"]], [c["reason"] for c in second["results"]])

    def test_two_dates_differ_and_message_names_busy_date(self):
        autumn = run(SCENARIOS["1. Плотная категория: ведущий на свадьбу, 15 октября"]["request"])
        winter = run(SCENARIOS["2. Тот же запрос 19 декабря — дело в занятости"]["request"])
        self.assertNotEqual([c["id"] for c in autumn["results"]], [c["id"] for c in winter["results"]])
        self.assertIn("кроме даты: 19 декабря у них занято", winter["message"])
        self.assertIn("19 декабря", winter["results"][0]["reason"])

    def test_explanations_are_not_interchangeable(self):
        for title, scenario in SCENARIOS.items():
            cards = run(scenario["request"])["results"]
            reasons = [c["reason"] for c in cards]
            with self.subTest(title):
                # DoD: erase the names — the cards must still be distinguishable.
                anonymous = [c["reason"].replace(c["name"], "___") for c in cards]
                self.assertEqual(len(set(anonymous)), len(anonymous))
                for reason in reasons:
                    self.assertFalse(any(p in reason.lower() for p in GENERIC_PHRASES))

    def test_ranking_differentiates_dense_category(self):
        results = run(SCENARIOS["1. Плотная категория: ведущий на свадьбу, 15 октября"]["request"])["results"]
        self.assertGreater(len({c["score"] for c in results}), 1)

    def test_comparative_facts_are_true(self):
        request = SCENARIOS["3. Фотограф на корпоратив, 15 октября"]["request"]
        results = run(request)["results"]
        cheapest = min(results, key=lambda c: c["price_from_kzt"])
        compare = next(f["text"] for f in cheapest["facts"] if f["id"] == "compare")
        self.assertIn("Самая низкая цена", compare)

    def test_empty_results_are_explained_in_words(self):
        busy = run(SCENARIOS["7. Пусто: все залы заняты 6 декабря"]["request"])
        self.assertIn("кроме даты: 6 декабря у них занято", busy["message"])
        self.assertIn("Ближайшие свободные дни", busy["message"])
        missing = run(SCENARIOS["9. Пусто: лайв-бэнда нет в Астане"]["request"])
        self.assertIn("Алматы", missing["message"])

    def test_llm_outage_falls_back_without_losing_reasons(self):
        response = run(SCENARIOS["1. Плотная категория: ведущий на свадьбу, 15 октября"]["request"], outage)
        self.assertEqual(len(response["results"]), 3)
        for card in response["results"]:
            self.assertEqual(card["reason_source"], "fallback")
            self.assertTrue(card["reason"])

    def test_one_comparison_call_for_whole_answer(self):
        request = SCENARIOS["3. Фотограф на корпоратив, 15 октября"]["request"]
        calls = []

        def select(payload):
            calls.append(payload)
            return [{"id": card["id"], "pros": [card["pros"][0]["id"]], "cons": []}
                    for card in payload["cards"]]

        with patch.dict("os.environ", {"OPENAI_API_KEY": "fake-test-key"}), \
             patch("comparison.openai_compare", side_effect=select), \
             patch("explanations.urlopen", side_effect=AssertionError("extra API call")):
            response = run(request)
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(calls[0]["cards"]), len(response["results"]))
        self.assertTrue(all(card["comparison_source"] == "llm" for card in response["results"]))

    def test_synthetic_profiles_are_labelled(self):
        for scenario in SCENARIOS.values():
            for card in run(scenario["request"])["results"]:
                if card["synthetic"]:
                    self.assertTrue(card["reason"].startswith("Синтетический профиль"))

    def test_reason_quotes_come_from_profile(self):
        for scenario in SCENARIOS.values():
            for card in run(scenario["request"])["results"]:
                for quote in re.findall(r"«([^»]{24,})»", card["reason"]):
                    self.assertIn(quote, " ".join(CATALOG[card["id"]]["description"].split()))

    def test_response_time(self):
        for scenario in SCENARIOS.values():
            self.assertLess(run(scenario["request"])["elapsed_ms"], 10_000)


if __name__ == "__main__":
    unittest.main()
