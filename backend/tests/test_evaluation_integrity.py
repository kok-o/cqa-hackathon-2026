from copy import deepcopy
import json
from pathlib import Path
import unittest

from backend.cqa.importer import normalize
from backend.cqa.models import ChecklistVersion, DatasetExport
from evaluation.metrics import summarize


FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


class EvaluationIntegrityTests(unittest.TestCase):
    def setUp(self):
        read = lambda name: json.loads((FIXTURES / name).read_text(encoding="utf-8"))
        conversation = normalize(DatasetExport.model_validate(read("synthetic_dev.json")))[0].model_dump()
        checklist = ChecklistVersion.model_validate(read("checklist.example.json")).model_dump()
        results = read("evaluation.example.json")["results"]
        self.labels = {
            "checklist": checklist,
            "items": [{"conversation": conversation, "split": "dev", "reviewed": True,
                       "expected_results": deepcopy(results), "expected_total": 100}],
        }
        self.prediction = {"conversation": deepcopy(conversation), "checklist": deepcopy(checklist),
                           "results": deepcopy(results), "score": {"total": 100}}

    def test_human_appeal_cannot_inflate_model_accuracy(self):
        self.prediction.update(revision=2, original_results=deepcopy(self.prediction["results"]),
                               original_score={"total": 66.67})
        self.prediction["original_results"][0]["answer"] = False
        report = summarize(self.labels, [self.prediction])
        self.assertEqual(report["decisions"]["agreement"], 0.5)
        self.assertAlmostEqual(report["total_mae"], 33.33)
        self.assertEqual(report["prediction_basis"], "original_model_outputs_before_appeals")

    def test_revised_export_without_originals_is_rejected(self):
        self.prediction["revision"] = 2
        with self.assertRaisesRegex(ValueError, "original model"):
            summarize(self.labels, [self.prediction])

    def test_matching_ids_cannot_hide_changed_transcript(self):
        self.prediction["conversation"]["transcript"][0]["text"] = "Changed after the gold labels were made"
        with self.assertRaisesRegex(ValueError, "different conversation"):
            summarize(self.labels, [self.prediction])

    def test_failed_prediction_does_not_contribute_a_stale_total(self):
        self.prediction["error"] = "failed"
        report = summarize(self.labels, [self.prediction])
        self.assertEqual(report["missing_or_failed_predictions"], 1)
        self.assertIsNone(report["total_mae"])


if __name__ == "__main__":
    unittest.main()
