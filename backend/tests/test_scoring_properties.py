"""Independent rational-arithmetic oracle over every official discrete combination."""
from fractions import Fraction
from itertools import product
from pathlib import Path
import random
import unittest

from backend.cqa.models import ChecklistVersion, CriterionResult
from backend.cqa.scoring import calculate_score


ROOT = Path(__file__).resolve().parents[2]


class ScoringPropertyTests(unittest.TestCase):
    def test_all_official_combinations_and_seeded_metamorphic_transforms(self):
        checklist = ChecklistVersion.model_validate_json((ROOT / "fixtures/checklist.official.json").read_text(encoding="utf-8"))
        choices = [([False, True] if c.answer_type == "boolean" else c.allowed_values) + ([None] if c.na_allowed else [])
                   for c in checklist.criteria]
        randomizer = random.Random(20261107)
        checked = 0
        for answers in product(*choices):
            earned, denominator = Fraction(0), Fraction(0)
            results = []
            for c, answer in zip(checklist.criteria, answers, strict=True):
                status = "not_applicable" if answer is None else "applicable"
                results.append(CriterionResult(criterion_id=c.criterion_id, status=status, answer=answer,
                    explanation="Independent discrete arithmetic check.",
                    checked_span={"start_message_id": "m1", "end_message_id": "m2"},
                    gross_error=c.criterion_id == "critical" and answer is True))
                if answer is not None:
                    weight = Fraction(str(c.weight))
                    normalized = Fraction(int(answer == c.positive_answer)) if c.answer_type == "boolean" else (
                        Fraction(str(answer)) - Fraction(str(c.scale_min))) / (Fraction(str(c.scale_max)) - Fraction(str(c.scale_min)))
                    if c.answer_type == "scale" and not c.higher_is_better:
                        normalized = 1 - normalized
                    earned += weight * normalized
                    denominator += weight
            hundredths = earned / denominator * 10000
            integer, remainder = divmod(hundredths.numerator, hundredths.denominator)
            expected = (integer + (remainder * 2 >= hundredths.denominator)) / 100
            actual = calculate_score(checklist, results)
            self.assertEqual(actual["total"], expected, answers)
            self.assertTrue(0 <= actual["total"] <= 100)
            self.assertEqual(actual["gross_error"], answers[-1])
            if checked % 37 == 0:
                shuffled = list(results)
                randomizer.shuffle(shuffled)
                scaled = checklist.model_copy(deep=True)
                factor = randomizer.choice([2, 3, 7, 10])
                for c in scaled.criteria:
                    c.weight *= factor
                self.assertEqual(calculate_score(checklist, shuffled)["total"], expected)
                self.assertEqual(calculate_score(scaled, shuffled)["total"], expected)
                uncertain = list(results)
                uncertain[0] = uncertain[0].model_copy(update={"answer": None, "status": "needs_review"})
                incomplete = calculate_score(checklist, uncertain)
                self.assertIsNone(incomplete["total"])
                self.assertEqual(incomplete["status"], "incomplete")
            checked += 1
        self.assertEqual(checked, 5184)


if __name__ == "__main__":
    unittest.main()
