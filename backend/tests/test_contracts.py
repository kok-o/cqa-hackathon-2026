from copy import deepcopy
import json
from pathlib import Path
import unittest

from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.cqa.evaluator.validation import validate_draft
from backend.cqa.importer import normalize
from backend.cqa.main import app
from backend.cqa.models import ChecklistVersion, DatasetExport, EvaluationDraft


FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


def read_fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.source = read_fixture("synthetic_dev.json")
        self.conversation = normalize(DatasetExport.model_validate(self.source))[0]
        self.checklist = ChecklistVersion.model_validate(read_fixture("checklist.example.json"))
        self.draft = read_fixture("evaluation.example.json")

    def test_source_order_and_timestamps_are_preserved(self):
        normalized = normalize(DatasetExport.model_validate(self.source))
        for raw, conversation in zip(self.source["data"]["records"], normalized, strict=True):
            self.assertEqual([m.timestamp for m in conversation.transcript], [m["timestamp"] for m in raw["transcript"]])
            self.assertEqual(conversation.transcript[0].message_id, raw["conversation_id"] + ":0000")
        self.assertEqual(normalized[2].transcript[0].timestamp, "2026-11-07T12:00:00+05:00")

    def test_count_and_duplicate_ids_are_rejected(self):
        self.source["data"]["total_records"] = 99
        with self.assertRaises(ValidationError):
            DatasetExport.model_validate(self.source)
        self.source["data"]["total_records"] = 3
        self.source["data"]["records"][1]["conversation_id"] = self.source["data"]["records"][0]["conversation_id"]
        with self.assertRaises(ValidationError):
            DatasetExport.model_validate(self.source)

    def test_valid_draft_and_real_quotes(self):
        validate_draft(self.conversation, self.checklist, EvaluationDraft.model_validate(self.draft))
        self.draft["results"][0]["evidence"][0]["quote"] = "Invented quote"
        with self.assertRaisesRegex(ValueError, "exact source quote"):
            validate_draft(self.conversation, self.checklist, EvaluationDraft.model_validate(self.draft))

    def test_criteria_version_and_allowed_na(self):
        changed = deepcopy(self.draft)
        changed["checklist_version"] = 2
        with self.assertRaisesRegex(ValueError, "version"):
            validate_draft(self.conversation, self.checklist, EvaluationDraft.model_validate(changed))
        changed = deepcopy(self.draft)
        changed["results"][0].update(status="not_applicable", answer=None)
        with self.assertRaisesRegex(ValueError, "N/A"):
            validate_draft(self.conversation, self.checklist, EvaluationDraft.model_validate(changed))

    def test_bounds_duplicates_and_missing_action_span(self):
        changed = deepcopy(self.draft)
        changed["results"][1]["answer"] = 3
        with self.assertRaisesRegex(ValueError, "scale"):
            validate_draft(self.conversation, self.checklist, EvaluationDraft.model_validate(changed))
        changed = deepcopy(self.draft)
        changed["results"].append(changed["results"][0])
        with self.assertRaisesRegex(ValueError, "exactly one"):
            validate_draft(self.conversation, self.checklist, EvaluationDraft.model_validate(changed))
        changed = deepcopy(self.draft)
        changed["results"][0].update(answer=False, evidence=[], checked_span={
            "start_message_id": "demo-ru-001:0003", "end_message_id": "demo-ru-001:0001",
        })
        with self.assertRaisesRegex(ValueError, "span"):
            validate_draft(self.conversation, self.checklist, EvaluationDraft.model_validate(changed))

    def test_normal_routes(self):
        client = TestClient(app)
        self.assertEqual(client.get("/health").json()["phase"], "ready")
        response = client.get("/api/demo/conversations")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 3)
        self.assertEqual(response.json()[0]["transcript"][1]["message_id"], "demo-ru-001:0001")


if __name__ == "__main__":
    unittest.main()
