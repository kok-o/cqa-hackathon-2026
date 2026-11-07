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


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.database = Path(self.temporary.name) / "cqa.sqlite3"
        self.calls = []
        self.app = create_app(self.database, evaluator=self.evaluate, evaluator_info={"provider_configured": True, "provider": "test", "model": "test", "mode": "test", "prompt_version": "test-v1"})
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.dataset = json.loads((ROOT / "fixtures/synthetic_dev.json").read_text(encoding="utf-8"))
        self.checklist = self.client.get("/api/checklists").json()[0]
        self.client.post("/api/datasets/import", json={"dataset": self.dataset}).raise_for_status()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.temporary.cleanup()

    async def evaluate(self, conversation, checklist):
        self.calls.append((conversation.model_dump(), checklist.model_dump()))
        message = next(m for m in conversation.transcript if m.speaker == "operator")
        results = []
        for criterion in checklist.criteria:
            answer = criterion.positive_answer if criterion.answer_type == "boolean" else criterion.scale_max if criterion.answer_type == "scale" else max(criterion.choices, key=criterion.choices.get)
            results.append({"criterion_id": criterion.criterion_id, "status": "applicable", "answer": answer, "explanation": "Изолированный тест API", "evidence": [{"message_id": message.message_id, "quote": message.text}]})
        return SimpleNamespace(draft=EvaluationDraft(conversation_id=conversation.conversation_id, group_id=conversation.group_id, checklist_id=checklist.checklist_id, checklist_version=checklist.version, prompt_version="test-v1", results=results), model_info={"provider": "test", "model": "test"})

    def job_body(self, **changes):
        return {"conversation_id": self.dataset["data"]["records"][0]["conversation_id"], "group_id": self.dataset["data"]["dataset_group_id"], "checklist_id": self.checklist["checklist_id"], "checklist_version": 1, **changes}

    def evaluate_one(self, **changes):
        response = self.client.post("/api/evaluation-jobs", json=self.job_body(**changes))
        self.assertEqual(response.status_code, 202, response.text)
        job = self.client.get("/api/evaluation-jobs/" + response.json()["job_id"]).json()
        self.assertEqual(job["status"], "completed", job)
        return self.client.get("/api/evaluations/" + job["evaluation_id"]).json()

    def appeal(self, evaluation, criterion=0, **extra):
        response = self.client.post(f"/api/evaluations/{evaluation['evaluation_id']}/appeals", json={"criterion_id": evaluation["results"][criterion]["criterion_id"], "comment": "Прошу проверить вывод", **extra})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def test_import_repeated_group_identity_and_filters(self):
        imported = self.client.post("/api/datasets/import", json={"dataset": self.dataset}).json()
        self.assertEqual(imported["unchanged"], 3)
        second = deepcopy(self.dataset)
        second["data"]["dataset_group_id"] = "CONTROL"
        self.assertEqual(self.client.post("/api/datasets/import", json={"dataset": second}).json()["imported"], 3)
        self.assertEqual(len(self.client.get("/api/conversations").json()), 6)
        self.assertEqual(len(self.client.get("/api/conversations", params={"group_id": "CONTROL", "language": "RU"}).json()), 1)
        detail = self.client.get("/api/conversations/" + self.job_body()["conversation_id"], params={"group_id": "CONTROL"}).json()
        self.assertEqual(detail["group_id"], "CONTROL")
        self.assertEqual(detail["transcript"][0]["timestamp"], second["data"]["records"][0]["transcript"][0]["timestamp"])

    def test_evaluation_new_data_snapshot_and_export(self):
        evaluation = self.evaluate_one()
        self.assertEqual(evaluation["score"]["total"], 100)
        self.assertEqual(evaluation["revision"], 1)
        self.assertEqual(evaluation["appeals"], [])
        changed = deepcopy(self.dataset)
        changed["data"]["records"][0]["transcript"][0]["text"] = "Новая версия разговора"
        self.assertEqual(self.client.post("/api/datasets/import", json={"dataset": changed}).json()["updated"], 1)
        unchanged = self.client.get("/api/evaluations/" + evaluation["evaluation_id"]).json()
        self.assertEqual(unchanged["conversation"], evaluation["conversation"])
        summaries = self.client.get("/api/conversations").json()
        summary = next(row for row in summaries if row["conversation_id"] == self.job_body()["conversation_id"])
        self.assertIsNone(summary["latest_evaluation_id"])
        newer = self.evaluate_one()
        self.assertEqual(newer["conversation"]["transcript"][0]["text"], "Новая версия разговора")
        exported = self.client.get("/api/evaluations/export")
        self.assertIn("attachment", exported.headers["content-disposition"])
        self.assertEqual(len(exported.json()), 2)

    def test_new_criterion_and_immutable_checklist_version(self):
        first = self.evaluate_one()
        configuration = {key: deepcopy(self.checklist[key]) for key in ("title", "provenance", "criteria")}
        configuration["criteria"].append({"criterion_id": "new-unseen", "title": "Новый критерий", "instruction": "Новая инструкция", "weight": 4, "answer_type": "boolean"})
        updated = self.client.post(f"/api/checklists/{self.checklist['checklist_id']}/versions", json=configuration)
        self.assertEqual(updated.status_code, 201, updated.text)
        self.assertEqual(updated.json()["version"], 2)
        second = self.evaluate_one(checklist_version=2)
        self.assertEqual(len(second["results"]), 3)
        self.assertEqual(second["results"][-1]["criterion_id"], "new-unseen")
        old = self.client.get("/api/evaluations/" + first["evaluation_id"]).json()
        self.assertEqual(len(old["checklist"]["criteria"]), 2)
        self.assertEqual(len(self.client.get("/api/checklists").json()), 2)

    def test_job_idempotency_checks_payload(self):
        first = self.evaluate_one(idempotency_key="job-one")
        second = self.evaluate_one(idempotency_key="job-one")
        self.assertEqual(first["evaluation_id"], second["evaluation_id"])
        self.assertEqual(len(self.calls), 1)
        body = self.job_body(idempotency_key="job-one", conversation_id=self.dataset["data"]["records"][1]["conversation_id"])
        self.assertEqual(self.client.post("/api/evaluation-jobs", json=body).status_code, 409)

    def test_appeal_recalculates_preserves_original_and_replays(self):
        evaluation = self.evaluate_one()
        appeal = self.appeal(evaluation, idempotency_key="appeal-one")
        replay = self.appeal(evaluation, idempotency_key="appeal-one")
        self.assertEqual(appeal["appeal_id"], replay["appeal_id"])
        replacement = deepcopy(evaluation["results"][0])
        replacement["answer"] = False
        replacement["explanation"] = "После пересмотра действие не обнаружено"
        replacement["evidence"] = []
        replacement["checked_span"] = {"start_message_id": evaluation["conversation"]["transcript"][0]["message_id"], "end_message_id": evaluation["conversation"]["transcript"][-1]["message_id"]}
        decision = {"action": "replace", "reason": "Основание проверено", "replacement_result": replacement, "expected_revision": 1, "idempotency_key": "decision-one"}
        response = self.client.post(f"/api/appeals/{appeal['appeal_id']}/decision", json=decision)
        self.assertEqual(response.status_code, 200, response.text)
        updated = response.json()
        self.assertEqual(updated["score"]["total"], 66.67)
        self.assertEqual(updated["original_score"]["total"], 100)
        self.assertTrue(updated["original_results"][0]["answer"])
        self.assertEqual(updated["revision"], 2)
        self.assertEqual(updated["appeals"][0]["status"], "resolved")
        replay = self.client.post(f"/api/appeals/{appeal['appeal_id']}/decision", json=decision)
        self.assertEqual(replay.json(), updated)
        decision["reason"] = "Другое основание"
        self.assertEqual(self.client.post(f"/api/appeals/{appeal['appeal_id']}/decision", json=decision).status_code, 409)

    def test_stale_review_and_invalid_evidence_are_atomic(self):
        evaluation = self.evaluate_one()
        first = self.appeal(evaluation)
        second = self.appeal(evaluation, criterion=1)
        invalid = deepcopy(evaluation["results"][0])
        invalid["evidence"][0]["quote"] = "Несуществующая цитата"
        response = self.client.post(f"/api/appeals/{first['appeal_id']}/decision", json={"action": "replace", "reason": "Проверка", "replacement_result": invalid, "expected_revision": 1})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.client.get("/api/evaluations/" + evaluation["evaluation_id"]).json()["revision"], 1)
        response = self.client.post(f"/api/appeals/{first['appeal_id']}/decision", json={"action": "uphold", "reason": "Оценка подтверждена", "expected_revision": 1})
        self.assertEqual(response.status_code, 200)
        response = self.client.post(f"/api/appeals/{second['appeal_id']}/decision", json={"action": "uphold", "reason": "Оценка подтверждена", "expected_revision": 1})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.client.get("/api/evaluations/" + evaluation["evaluation_id"]).json()["appeals"][1]["status"], "open")

    def test_failure_is_visible_without_provider_secrets(self):
        async def failing(conversation, checklist):
            raise RuntimeError("SECRET_KEY and https://signed.example/?token=private")
        with TestClient(create_app(Path(self.temporary.name) / "failed.sqlite3", evaluator=failing)) as client:
            client.post("/api/datasets/import", json={"dataset": self.dataset})
            job = client.post("/api/evaluation-jobs", json=self.job_body()).json()
            state = client.get("/api/evaluation-jobs/" + job["job_id"]).json()
            self.assertEqual(state["status"], "failed")
            self.assertNotIn("SECRET", state["error"])
            self.assertNotIn("signed", state["error"])
            self.assertEqual(client.get("/api/evaluations").json(), [])

    def test_restart_preserves_results_and_marks_interrupted_job(self):
        evaluation = self.evaluate_one()
        store = self.app.state.store
        conversation = store.conversation(self.job_body()["group_id"], self.job_body()["conversation_id"])
        checklist = store.checklist(self.checklist["checklist_id"], 1)
        job, _ = store.create_job(conversation, checklist, "interrupted")
        store.begin_job(job["job_id"])
        self.client.__exit__(None, None, None)
        self.app = create_app(self.database, evaluator=self.evaluate)
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.assertEqual(self.client.get("/api/evaluations/" + evaluation["evaluation_id"]).json()["score"]["total"], 100)
        restarted = self.client.get("/api/evaluation-jobs/" + job["job_id"]).json()
        self.assertEqual(restarted["status"], "failed")
        self.assertIn("перезапущен", restarted["error"])

    def test_invalid_requests_missing_objects_and_api_fallback(self):
        self.assertEqual(self.client.post("/api/datasets/import", json={"source": "demo", "dataset": self.dataset}).status_code, 422)
        bad = deepcopy(self.dataset)
        bad["data"]["total_records"] = 100
        self.assertEqual(self.client.post("/api/datasets/import", json={"dataset": bad}).status_code, 422)
        self.assertEqual(self.client.get("/api/conversations/missing", params={"group_id": "DEV"}).status_code, 404)
        self.assertEqual(self.client.get("/api/no-such-endpoint").status_code, 404)
        self.assertEqual(self.client.get("/assets/missing.js").status_code, 404)
        self.assertEqual(self.client.post("/api/evaluation-jobs", json=self.job_body(checklist_version=99)).status_code, 404)


if __name__ == "__main__":
    unittest.main()
