from copy import deepcopy
import unittest

from backend.cqa.models import ChecklistVersion, CriterionResult
from backend.cqa.scoring import calculate_score


def result(identifier, answer=True, status="applicable", gross_error=False):
    return CriterionResult(
        criterion_id=identifier, status=status, answer=answer,
        explanation="Проверен разговор", checked_span={"start_message_id": "m1", "end_message_id": "m2"},
        gross_error=gross_error,
    )


class ScoringTests(unittest.TestCase):
    def setUp(self):
        self.configuration = {
            "checklist_id": "test", "version": 1, "title": "Учебные правила", "provenance": "synthetic_example",
            "criteria": [
                {"criterion_id": "a", "title": "A", "instruction": "Проверить A", "weight": 1, "answer_type": "boolean", "na_allowed": True, "section_id": "service"},
                {"criterion_id": "b", "title": "B", "instruction": "Проверить B", "weight": 2, "answer_type": "scale", "scale_min": 1, "scale_max": 5, "na_allowed": True, "section_id": "service"},
            ],
        }

    def score(self, results, configuration=None):
        return calculate_score(ChecklistVersion.model_validate(configuration or self.configuration), results)

    def test_maximum_minimum_and_weighted_scale(self):
        self.assertEqual(self.score([result("a"), result("b", 5)])["total"], 100)
        self.assertEqual(self.score([result("a", False), result("b", 1)])["total"], 0)
        score = self.score([result("a", False), result("b", 3)])
        self.assertEqual(score["total"], 33.33)
        self.assertEqual(score["earned_weight"], 1)
        self.assertEqual(score["applicable_weight"], 3)
        self.assertAlmostEqual(score["criteria"][1]["contribution"], 100 / 3, places=5)
        self.assertEqual(score["sections"]["service"], 33.33)

    def test_na_redistributes_and_all_na_has_no_score(self):
        score = self.score([result("a", None, "not_applicable"), result("b", 3)])
        self.assertEqual(score["total"], 50)
        self.assertEqual(score["applicable_weight"], 2)
        all_na = self.score([result("a", None, "not_applicable"), result("b", None, "not_applicable")])
        self.assertIsNone(all_na["total"])
        self.assertEqual(all_na["status"], "not_applicable")
        self.assertEqual(all_na["coverage"], 1)

    def test_unknown_never_raises_total_by_excluding_weight(self):
        score = self.score([result("a", None, "needs_review"), result("b", 5)])
        self.assertIsNone(score["total"])
        self.assertEqual(score["status"], "incomplete")
        self.assertEqual(score["applicable_weight"], 3)
        self.assertEqual(score["coverage"], .5)
        self.assertIsNone(score["sections"]["service"])
        self.assertTrue(all(row["contribution"] is None for row in score["criteria"]))

    def test_inverted_boolean_scale_and_choice(self):
        configuration = deepcopy(self.configuration)
        configuration["criteria"][0]["positive_answer"] = False
        configuration["criteria"][1]["higher_is_better"] = False
        self.assertEqual(self.score([result("a", False), result("b", 1)], configuration)["total"], 100)
        configuration["criteria"].append({"criterion_id": "c", "title": "C", "instruction": "C", "weight": 1, "answer_type": "choice", "choices": {"good": 1, "partial": .5, "bad": 0}})
        self.assertEqual(self.score([result("a", False), result("b", 1), result("c", "partial")], configuration)["total"], 87.5)

    def test_zero_weights_and_gross_error_are_not_invented_penalties(self):
        score = self.score([result("a", True, gross_error=True), result("b", 5)])
        self.assertTrue(score["gross_error"])
        self.assertEqual(score["total"], 100)
        configuration = deepcopy(self.configuration)
        for criterion in configuration["criteria"]:
            criterion["weight"] = 0
        score = self.score([result("a"), result("b", 5)], configuration)
        self.assertEqual(score["status"], "not_applicable")
        self.assertIsNone(score["total"])

    def test_invalid_results_are_rejected(self):
        for results in ([result("a")], [result("a"), result("a")], [result("a", "yes"), result("b", 5)], [result("a"), result("b", 6)]):
            with self.assertRaises(ValueError):
                self.score(results)
        configuration = deepcopy(self.configuration)
        configuration["criteria"][0]["na_allowed"] = False
        with self.assertRaises(ValueError):
            self.score([result("a", None, "not_applicable"), result("b", 5)], configuration)


if __name__ == "__main__":
    unittest.main()
