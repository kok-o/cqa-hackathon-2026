from copy import deepcopy
import unittest
from pathlib import Path

from evaluation.regression import compare
from evaluation.benchmark import fingerprint, read_json
from backend.cqa.models import ChecklistVersion, DatasetExport

ROOT = Path(__file__).resolve().parents[2]


class AuthoredRegressionTests(unittest.TestCase):
    def setUp(self):
        conversation = {"group_id": "SYNTHETIC", "conversation_id": "case1"}
        result = {"criterion_id": "contact", "status": "applicable", "answer": True}
        self.expected = {"provenance": "synthetic_regression_authored_by_assistant", "not_ground_truth": True,
                         "checklist_sha256": "rubric", "dataset_sha256": "dataset", "items": [
                             {**conversation, "scenario": "example", "expected_results": [result]}]}
        self.manifest = {"fingerprint": "run", "inputs": {"models": {"model": {}}, "checklist_sha256": "rubric",
                         "datasets": [{"sha256": "dataset"}]}, "tasks": [{"model": "model", "conversation": conversation}]}
        self.checkpoint = {"records": {"one": {"model": "model", "conversation": conversation, "status": "completed",
                           "error": None, "original_results": [dict(result, answer=False)], "results": [result]}}}

    def test_appeal_changes_do_not_make_authored_regression_pass(self):
        report = compare(self.expected, self.manifest, self.checkpoint)
        self.assertEqual(report["models"][0]["passed_decisions"], 0)
        self.assertIn("not_human_or_organizer_accuracy", report["metric_status"])
        self.assertTrue(report["not_ground_truth"])

    def test_missing_predictions_fail_checked_expectations(self):
        report = compare(self.expected, self.manifest, {"records": {}})
        self.assertEqual(report["models"][0]["checked_decisions"], 1)
        self.assertEqual(report["models"][0]["authored_regression_pass_rate"], 0)

    def test_cannot_change_fixture_or_relabel_authored_assertions(self):
        expected = deepcopy(self.expected)
        expected["dataset_sha256"] = "different"
        with self.assertRaisesRegex(ValueError, "different synthetic"):
            compare(expected, self.manifest, self.checkpoint)
        expected = deepcopy(self.expected)
        expected["provenance"] = "human_review"
        with self.assertRaisesRegex(ValueError, "assistant-authored"):
            compare(expected, self.manifest, self.checkpoint)

    def test_authored_fixture_matches_exact_official_rubric_and_balanced_dataset(self):
        dataset = read_json(ROOT / "fixtures/regression/dataset.json")
        export = DatasetExport.model_validate(dataset)
        expected = read_json(ROOT / "fixtures/regression/expected.json")
        checklist = ChecklistVersion.model_validate(read_json(ROOT / "fixtures/checklist.official.json"))
        self.assertEqual(expected["dataset_sha256"], fingerprint(dataset))
        self.assertEqual(expected["checklist_sha256"], fingerprint(checklist.model_dump(mode="json")))
        self.assertEqual(len(export.data.records), 6)
        for language in ("RU", "KZ", "EN"):
            self.assertEqual(sum(r.language == language for r in export.data.records), 2)
        criteria = {c.criterion_id: c for c in checklist.criteria}
        for item in expected["items"]:
            self.assertEqual([r["criterion_id"] for r in item["expected_results"]], list(criteria))
            for result in item["expected_results"]:
                criterion = criteria[result["criterion_id"]]
                if result["status"] == "not_applicable":
                    self.assertTrue(criterion.na_allowed)
                    self.assertIsNone(result["answer"])
                elif criterion.answer_type == "boolean":
                    self.assertIs(type(result["answer"]), bool)
                else:
                    self.assertIn(result["answer"], criterion.allowed_values)
                self.assertEqual(result["gross_error"], criterion.criterion_id == "critical" and result["answer"] is True)


if __name__ == "__main__":
    unittest.main()
