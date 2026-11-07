from copy import deepcopy
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import tempfile
import unittest
from pathlib import Path

from evaluation.regression import compare, main
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

    def test_mixed_groups_require_explicit_filter_and_keep_all_48_assertions(self):
        expected = read_json(ROOT / "fixtures/regression/expected.json")
        group = expected["items"][0]["group_id"]
        manifest = {"fingerprint": "mixed-run", "inputs": {"models": {"model": {}},
            "checklist_sha256": expected["checklist_sha256"],
            "datasets": [{"sha256": expected["dataset_sha256"]}, {"sha256": "dev-dataset"}]}, "tasks": []}
        checkpoint = {"records": {}}
        for index, item in enumerate(expected["items"]):
            conversation = {k: item[k] for k in ("group_id", "conversation_id")}
            manifest["tasks"].append({"model": "model", "conversation": conversation})
            checkpoint["records"][f"synthetic-{index}"] = {"model": "model", "conversation": conversation,
                "status": "completed", "original_results": deepcopy(item["expected_results"])}
            dev_conversation = {"group_id": "DEV", "conversation_id": f"dev-{index}"}
            manifest["tasks"].append({"model": "model", "conversation": dev_conversation})
            checkpoint["records"][f"dev-{index}"] = {"model": "model", "conversation": dev_conversation,
                "status": "completed", "original_results": []}
        with self.assertRaisesRegex(ValueError, "no regression expectation"):
            compare(expected, manifest, checkpoint)
        snapshots = deepcopy((expected, manifest, checkpoint))
        report = compare(expected, manifest, checkpoint, group=group)
        self.assertEqual(report["filtered_group"], group)
        self.assertEqual(report["skipped_planned_tasks"], 6)
        self.assertEqual(report["skipped_records"], 6)
        self.assertEqual(report["selected_records"], 6)
        self.assertEqual(report["models"][0]["checked_decisions"], 48)
        self.assertEqual(report["models"][0]["passed_decisions"], 48)
        self.assertEqual((expected, manifest, checkpoint), snapshots)

    def test_empty_or_unmatched_group_is_rejected(self):
        for group in ("", "   ", "ABSENT"):
            with self.subTest(group=group):
                with self.assertRaisesRegex(ValueError, "group"):
                    compare(self.expected, self.manifest, self.checkpoint, group=group)

    def test_group_does_not_hide_unknown_planned_or_checkpoint_conversations(self):
        unknown = {"group_id": "SYNTHETIC", "conversation_id": "missing-expectation"}
        for location in ("planned", "checkpoint"):
            with self.subTest(location=location):
                manifest, checkpoint = deepcopy(self.manifest), deepcopy(self.checkpoint)
                if location == "planned":
                    manifest["tasks"].append({"model": "model", "conversation": unknown})
                else:
                    checkpoint["records"]["unknown"] = {"model": "model", "conversation": unknown, "status": "failed"}
                with self.assertRaisesRegex(ValueError, "no regression expectation"):
                    compare(self.expected, manifest, checkpoint, group="SYNTHETIC")

    def test_group_with_no_predictions_still_counts_missing_assertions(self):
        report = compare(self.expected, self.manifest, {"records": {}}, group="SYNTHETIC")
        self.assertEqual(report["models"][0]["checked_decisions"], 1)
        self.assertEqual(report["models"][0]["passed_decisions"], 0)

    def test_cli_group_is_explicit_in_saved_report_and_summary(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            for filename, document in (("expected.json", self.expected), ("manifest.json", self.manifest), ("checkpoint.json", self.checkpoint)):
                (folder / filename).write_text(json.dumps(document), encoding="utf-8")
            output = io.StringIO()
            with redirect_stdout(output), redirect_stderr(io.StringIO()):
                code = main(["--expected", str(folder / "expected.json"), "--run-dir", str(folder), "--group", "SYNTHETIC"])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(output.getvalue())["filtered_group"], "SYNTHETIC")
            self.assertEqual(read_json(folder / "regression-report.json")["filtered_group"], "SYNTHETIC")
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
