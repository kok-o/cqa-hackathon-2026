"""Contract and arithmetic checks against the organizer's three-page checklist.

These inputs exercise the rubric, not model quality or official dataset labels.
"""

from copy import deepcopy
from pathlib import Path
import unittest

from backend.cqa.evaluator.validation import validate_draft
from backend.cqa.importer import normalize
from backend.cqa.models import ChecklistVersion, CriterionResult, DatasetExport, EvaluationDraft
from backend.cqa.scoring import calculate_score


FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


class OfficialChecklistTests(unittest.TestCase):
    def setUp(self):
        self.checklist = ChecklistVersion.model_validate_json(
            (FIXTURES / "checklist.official.json").read_text(encoding="utf-8")
        )
        self.conversation = normalize(DatasetExport.model_validate_json(
            (FIXTURES / "synthetic_dev.json").read_text(encoding="utf-8")
        ))[0]
        self.span = {
            "start_message_id": self.conversation.transcript[0].message_id,
            "end_message_id": self.conversation.transcript[-1].message_id,
        }

    def results(self, **answers):
        result = []
        for criterion in self.checklist.criteria:
            default = criterion.positive_answer if criterion.answer_type == "boolean" else criterion.scale_max
            answer = answers.get(criterion.criterion_id, default)
            result.append(CriterionResult(
                criterion_id=criterion.criterion_id,
                status="not_applicable" if answer is None else "applicable",
                answer=answer, explanation="Изолированный пример расчёта по официальному чек-листу",
                checked_span=self.span,
                gross_error=criterion.criterion_id == "critical" and answer is True,
            ))
        return result

    def validate(self, results):
        validate_draft(self.conversation, self.checklist, EvaluationDraft(
            conversation_id=self.conversation.conversation_id,
            group_id=self.conversation.group_id,
            checklist_id=self.checklist.checklist_id,
            checklist_version=self.checklist.version,
            prompt_version="isolated-rubric-test", results=results,
        ))

    def test_official_order_weights_and_na_permissions(self):
        # Checklist p. 1; exact export IDs are specified in the appendix p. 3.
        self.assertEqual(
            [(c.criterion_id, c.weight) for c in self.checklist.criteria],
            [("contact", 10), ("discovery", 15), ("identification", 20),
             ("consultation", 20), ("empathy", 15), ("dialogue", 15),
             ("closure", 5), ("critical", 0)],
        )
        self.assertEqual(sum(c.weight for c in self.checklist.criteria), 100)
        self.assertEqual({c.criterion_id for c in self.checklist.criteria if c.na_allowed},
                         {"discovery", "identification", "consultation", "closure"})
        self.assertEqual(self.checklist.provenance, "organizer")

    def test_dialogue_scale_is_proportional_to_fifteen_point_weight(self):
        # Checklist p. 2: the maximum scale step must give the complete weight.
        for answer, expected_contribution, expected_total in ((10, 15, 100), (7, 10.5, 95.5), (4, 6, 91), (0, 0, 85)):
            with self.subTest(answer=answer):
                results = self.results(dialogue=answer)
                self.validate(results)
                score = calculate_score(self.checklist, results)
                row = next(item for item in score["criteria"] if item["criterion_id"] == "dialogue")
                self.assertEqual(row["contribution"], expected_contribution)
                self.assertEqual(score["total"], expected_total)

    def test_only_published_consultation_and_dialogue_steps_are_valid(self):
        for criterion_id, valid, invalid in (
            ("consultation", (0, 5, 10, 15, 20), (1, 7, 12.5, 19)),
            ("dialogue", (0, 4, 7, 10), (1, 5, 7.5, 9)),
        ):
            criterion = next(c for c in self.checklist.criteria if c.criterion_id == criterion_id)
            self.assertEqual(criterion.allowed_values, list(valid))
            for answer in valid:
                with self.subTest(criterion=criterion_id, valid=answer):
                    self.validate(self.results(**{criterion_id: answer}))
            for answer in invalid:
                with self.subTest(criterion=criterion_id, invalid=answer):
                    results = self.results(**{criterion_id: answer})
                    with self.assertRaisesRegex(ValueError, "Answer must be one of"):
                        self.validate(results)
                    with self.assertRaisesRegex(ValueError, "ступенью"):
                        calculate_score(self.checklist, results)

    def test_critical_flag_matches_answer_and_does_not_zero_the_score(self):
        # Appendix p. 3: critical_flag equals critical.value, without zeroing.
        for flag in (False, True):
            with self.subTest(flag=flag):
                results = self.results(critical=flag)
                self.validate(results)
                score = calculate_score(self.checklist, results)
                self.assertEqual(score["total"], 100)
                self.assertIs(score["gross_error"], flag)
                inconsistent = deepcopy(results)
                inconsistent[-1].gross_error = not flag
                with self.assertRaisesRegex(ValueError, "Gross-error flag must match"):
                    self.validate(inconsistent)

    def test_na_redistributes_globally_and_final_rounding_is_half_up(self):
        # C3 N/A removes 20 weight. Earned points are 10 + 20 + (7/10*15) = 40.5.
        # 100 * 40.5 / 80 = 50.625; Telegram #2978 confirms 50.63 (half up).
        results = self.results(discovery=False, identification=None, empathy=False, dialogue=7, closure=False)
        self.validate(results)
        score = calculate_score(self.checklist, results)
        self.assertEqual(score["applicable_weight"], 80)
        self.assertEqual(score["earned_weight"], 40.5)
        self.assertEqual(score["total"], 50.63)
        self.assertEqual(score["status"], "complete")
        self.assertEqual(next(row for row in score["criteria"] if row["criterion_id"] == "contact")["contribution"], 12.5)

    def test_appendix_structural_example_recalculates_to_85_38(self):
        # The appendix p. 2 explicitly labels this a fictional format example.
        results = self.results(discovery=None, identification=None, consultation=15, dialogue=7)
        self.validate(results)
        score = calculate_score(self.checklist, results)
        self.assertEqual(score["applicable_weight"], 65)
        self.assertEqual(score["earned_weight"], 55.5)
        self.assertEqual(score["total"], 85.38)

    def test_na_is_rejected_outside_the_four_published_conditions(self):
        for criterion_id in ("contact", "empathy", "dialogue", "critical"):
            with self.subTest(criterion=criterion_id):
                results = self.results(**{criterion_id: None})
                with self.assertRaisesRegex(ValueError, "N/A is not allowed"):
                    self.validate(results)
                with self.assertRaisesRegex(ValueError, "N/A не разрешён"):
                    calculate_score(self.checklist, results)


if __name__ == "__main__":
    unittest.main()
