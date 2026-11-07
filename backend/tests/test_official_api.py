from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from fastapi.testclient import TestClient

from backend.cqa.main import create_app
from backend.cqa.models import EvaluationDraft


ROOT = Path(__file__).resolve().parents[2]


class OfficialApiTests(unittest.TestCase):
    def test_default_rubric_and_export_preserve_selected_model_and_missing_results(self):
        source = json.loads((ROOT / "fixtures/synthetic_dev.json").read_text(encoding="utf-8"))
        records = []
        for index in range(10):
            record = deepcopy(source["data"]["records"][0])
            record["conversation_id"] = f"official-api-{index}"
            records.append(record)
        source["data"] = {"dataset_group_id": "PROD_TEST", "total_records": 10, "records": records}
        chosen = {"model": "gpt-6.1-sol", "contact": True}

        async def evaluator(conversation, checklist):
            operator = next(m for m in conversation.transcript if m.speaker == "operator")
            results = []
            for criterion in checklist.criteria:
                answer = criterion.scale_max if criterion.answer_type == "scale" else criterion.positive_answer
                if criterion.criterion_id == "contact":
                    answer = chosen["contact"]
                results.append({"criterion_id": criterion.criterion_id, "status": "applicable",
                    "answer": answer, "explanation": "Synthetic API wiring check, not a real evaluation.",
                    "evidence": [{"message_id": operator.message_id, "quote": operator.text}], "gross_error": False})
            return SimpleNamespace(draft=EvaluationDraft(conversation_id=conversation.conversation_id,
                group_id=conversation.group_id, checklist_id=checklist.checklist_id,
                checklist_version=checklist.version, prompt_version="test", results=results),
                model_info={"model": chosen["model"], "requested_model": chosen["model"], "mode": "test"})

        with TemporaryDirectory() as temporary:
            with TestClient(create_app(Path(temporary) / "test.sqlite3", evaluator=evaluator)) as client:
                rules = client.get("/api/checklists").json()[0]
                self.assertEqual(rules["provenance"], "organizer")
                self.assertEqual(len(rules["criteria"]), 8)
                client.post("/api/datasets/import", json={"dataset": source}).raise_for_status()
                body = {"group_id": "PROD_TEST", "conversation_id": "official-api-0",
                        "checklist_id": rules["checklist_id"], "checklist_version": 1}
                client.post("/api/evaluation-jobs", json=body).raise_for_status()
                chosen.update(model="gpt-6-luna", contact=False)
                client.post("/api/evaluation-jobs", json=body).raise_for_status()
                params = {"group_id": "PROD_TEST", "model_name": "gpt-6.1-sol", "split": "prod", "team_name": "SetW", "model_mode": "external_api"}
                response = client.get("/api/submission/export", params=params)
                self.assertEqual(response.status_code, 200, response.text)
                document = response.json()
                self.assertEqual(document["schema_version"], "1.0")
                self.assertEqual(document["team_name"], "SetW")
                self.assertEqual(document["model"]["name"], "gpt-6.1-sol")
                self.assertEqual(len(document["results"]), 10)
                first = next(r for r in document["results"] if r["conversation_id"] == "official-api-0")
                self.assertEqual(first["score"]["final_score"], 100)
                self.assertEqual(sum(r["status"] == "error" for r in document["results"]), 9)
                bad = client.get("/api/submission/export", params={**params, "split": "dev"})
                self.assertEqual(bad.status_code, 422)


if __name__ == "__main__":
    unittest.main()
