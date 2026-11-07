import asyncio
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import quote

import httpx

from backend.cqa.main import create_app
from backend.cqa.evaluator.service import EvaluatorError
from backend.cqa.models import EvaluationDraft


ROOT = Path(__file__).resolve().parents[2]


class BackendReliabilityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = TemporaryDirectory()
        self.calls = []
        self.fail_once = set()
        self.active = self.peak = 0
        self.hold_once = set()
        self.entered, self.release = asyncio.Event(), asyncio.Event()
        self.reported_failure = None
        with patch.dict("os.environ", {"LLM_CONCURRENCY": "2"}):
            self.app = create_app(Path(self.temporary.name) / "test.sqlite3", evaluator=self.evaluate)
        self.lifecycle = self.app.router.lifespan_context(self.app)
        await self.lifecycle.__aenter__()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test")
        self.source = json.loads((ROOT / "fixtures/synthetic_dev.json").read_text(encoding="utf-8"))
        self.rules = (await self.client.get("/api/checklists")).json()[0]

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.lifecycle.__aexit__(None, None, None)
        self.temporary.cleanup()

    async def evaluate(self, conversation, checklist):
        self.calls.append(conversation.conversation_id)
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            if conversation.conversation_id in self.hold_once:
                self.hold_once.remove(conversation.conversation_id)
                self.entered.set()
                await self.release.wait()
            if self.reported_failure is not None:
                raise self.reported_failure
            await asyncio.sleep(0.005)
            if conversation.conversation_id in self.fail_once:
                self.fail_once.remove(conversation.conversation_id)
                raise RuntimeError("TEST_SECRET_MUST_NOT_ESCAPE_JOB_FAILURE")
            operator = next(m for m in conversation.transcript if m.speaker == "operator")
            results = [{"criterion_id": c.criterion_id, "status": "applicable",
                        "answer": c.scale_max if c.answer_type == "scale" else c.positive_answer,
                        "explanation": "Mocked transport check; not a model quality assessment.",
                        "evidence": [{"message_id": operator.message_id, "quote": operator.text}]}
                       for c in checklist.criteria]
            draft = EvaluationDraft(conversation_id=conversation.conversation_id, group_id=conversation.group_id,
                                    checklist_id=checklist.checklist_id, checklist_version=checklist.version,
                                    prompt_version="test", results=results)
            return SimpleNamespace(draft=draft, model_info={"model": "test", "requested_model": "test", "provider": "test"})
        finally:
            self.active -= 1

    def dataset(self, ids, group="UNSEEN_TEST"):
        records = []
        for identity in ids:
            record = deepcopy(self.source["data"]["records"][0])
            record["conversation_id"] = identity
            records.append(record)
        return {"isSuccess": True, "error": "", "data": {"dataset_group_id": group,
                "total_records": len(records), "records": records}}

    def job_body(self, identity, group="UNSEEN_TEST", key=None):
        return {"conversation_id": identity, "group_id": group, "checklist_id": self.rules["checklist_id"],
                "checklist_version": self.rules["version"], "idempotency_key": key}

    async def submit(self, identity, group="UNSEEN_TEST", key=None):
        response = await self.client.post("/api/evaluation-jobs", json=self.job_body(identity, group, key))
        self.assertEqual(response.status_code, 202, response.text)
        state = await self.client.get("/api/evaluation-jobs/" + response.json()["job_id"])
        self.assertEqual(state.status_code, 200)
        return state.json()

    async def test_encoded_source_ids_roundtrip_and_submission(self):
        ids = ["ru/new", "ru?case#1", "kz % белгі", *[f"new-{n}" for n in range(7)]]
        group = "group / ? # 100%"
        dataset = self.dataset(ids, group)
        imported = await self.client.post("/api/datasets/import", json={"dataset": dataset})
        self.assertEqual(imported.status_code, 200, imported.text)
        for identity in ids:
            response = await self.client.get("/api/conversations/" + quote(identity, safe=""), params={"group_id": group})
            self.assertEqual(response.status_code, 200, (identity, response.text))
            self.assertEqual(response.json()["conversation_id"], identity)
            self.assertEqual(response.json()["transcript"][0]["message_id"], identity + ":0000")
        states = await asyncio.gather(*(self.submit(identity, group, f"encoded-{index}") for index, identity in enumerate(ids)))
        self.assertTrue(all(s["status"] == "completed" for s in states))
        exported = await self.client.get("/api/submission/export", params={"group_id": group, "model_name": "test",
                "team_name": "Synthetic test", "model_mode": "external_api", "split": "prod"})
        self.assertEqual(exported.status_code, 200, exported.text)
        self.assertEqual({r["conversation_id"] for r in exported.json()["results"]}, set(ids))
        self.assertTrue(all(len(r["criteria"]) == 8 for r in exported.json()["results"]))

    async def test_empty_content_and_blank_ids_are_rejected_atomically(self):
        for malformed in ("empty_transcript", "blank_messages", "blank_id", "blank_group"):
            with self.subTest(malformed=malformed):
                dataset = self.dataset(["valid", "invalid"])
                invalid = dataset["data"]["records"][1]
                if malformed == "empty_transcript":
                    invalid["transcript"] = []
                elif malformed == "blank_messages":
                    for message in invalid["transcript"]:
                        message["text"] = " \t\n"
                elif malformed == "blank_id":
                    invalid["conversation_id"] = " \t"
                else:
                    dataset["data"]["dataset_group_id"] = " \t"
                response = await self.client.post("/api/datasets/import", json={"dataset": dataset})
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual((await self.client.get("/api/conversations")).json(), [])
        self.assertEqual(self.calls, [])

    async def test_empty_segments_are_preserved_when_dialogue_has_content(self):
        dataset = self.dataset(["blank-segment"])
        dataset["data"]["records"][0]["transcript"][0]["text"] = " \n"
        response = await self.client.post("/api/datasets/import", json={"dataset": dataset})
        self.assertEqual(response.status_code, 200, response.text)
        saved = (await self.client.get("/api/conversations/blank-segment", params={"group_id": "UNSEEN_TEST"})).json()
        self.assertEqual(saved["transcript"][0]["text"], " \n")

    async def test_upsert_reports_retained_ids_without_deleting_source_or_history(self):
        first = await self.client.post("/api/datasets/import", json={"dataset": self.dataset(["a", "b"])})
        self.assertEqual(first.json()["current_group_total"], 2)
        self.assertEqual(first.json()["retained_from_previous_import"], 0)
        state = await self.submit("a")
        second = await self.client.post("/api/datasets/import", json={"dataset": self.dataset(["b", "c"])})
        self.assertEqual(second.json()["total"], 2)
        self.assertEqual(second.json()["current_group_total"], 3)
        self.assertEqual(second.json()["retained_from_previous_import"], 1)
        self.assertEqual(second.json()["unchanged"], 1)
        self.assertEqual(second.json()["imported"], 1)
        self.assertEqual((await self.client.get("/api/evaluations/" + state["evaluation_id"])).status_code, 200)

    async def test_parallel_chat_and_voice_jobs_isolate_failures_and_replay_keys(self):
        ids = [f"new-{n}" for n in range(10)] + ["new-voice"]
        dataset = self.dataset(ids)
        dataset["data"]["records"][-1]["channel"] = "voice call"
        dataset["data"]["records"][-1]["audio_url"] = None
        (await self.client.post("/api/datasets/import", json={"dataset": dataset})).raise_for_status()
        self.fail_once.update({ids[2], ids[-1]})
        states = await asyncio.gather(*(self.submit(identity, key="batch-" + identity) for identity in ids))
        self.assertEqual(sum(s["status"] == "completed" for s in states), 9)
        self.assertEqual(sum(s["status"] == "failed" for s in states), 2)
        self.assertNotIn("TEST_SECRET", json.dumps(states))
        self.assertEqual(self.peak, 2)
        repeated = await asyncio.gather(*(self.submit(identity, key="batch-" + identity) for identity in ids))
        self.assertEqual(repeated, states)
        self.assertEqual(len(self.calls), 11)
        for identity in (ids[2], ids[-1]):
            self.assertEqual((await self.submit(identity, key="retry-" + identity))["status"], "completed")
        evaluations = (await self.client.get("/api/evaluations")).json()
        self.assertEqual(len(evaluations), 11)
        self.assertTrue(all(len(item["original_results"]) == 8 for item in evaluations))
        voice = next(item for item in evaluations if item["conversation"]["conversation_id"] == "new-voice")
        self.assertEqual(voice["conversation"]["channel"], "voice call")
        self.assertIsNone(voice["conversation"]["audio_url"])

    async def test_concurrent_duplicate_job_key_dispatches_once(self):
        (await self.client.post("/api/datasets/import", json={"dataset": self.dataset(["once"])})).raise_for_status()
        responses = await asyncio.gather(*(self.client.post("/api/evaluation-jobs", json=self.job_body("once", key="shared-key"))
                                           for _ in range(8)))
        self.assertTrue(all(response.status_code == 202 for response in responses))
        job_ids = {response.json()["job_id"] for response in responses}
        self.assertEqual(len(job_ids), 1)
        job = (await self.client.get("/api/evaluation-jobs/" + job_ids.pop())).json()
        self.assertEqual(job["status"], "completed")
        self.assertEqual(self.calls, ["once"])
        self.assertEqual(len((await self.client.get("/api/evaluations")).json()), 1)

    async def test_late_old_snapshot_cannot_hide_current_evaluation_or_official_export(self):
        dataset = self.dataset(["changing"] + [f"other-{n}" for n in range(9)])
        (await self.client.post("/api/datasets/import", json={"dataset": dataset})).raise_for_status()
        self.hold_once.add("changing")
        pending = asyncio.create_task(self.submit("changing", key="old"))
        await asyncio.wait_for(self.entered.wait(), timeout=2)
        try:
            dataset["data"]["records"][0]["transcript"][0]["text"] = "Current revised source"
            (await self.client.post("/api/datasets/import", json={"dataset": dataset})).raise_for_status()
            current = await self.submit("changing", key="current")
        finally:
            self.release.set()
        old = await pending
        summary = next(item for item in (await self.client.get("/api/conversations")).json() if item["conversation_id"] == "changing")
        self.assertEqual(summary["latest_evaluation_id"], current["evaluation_id"])
        self.assertNotEqual(old["evaluation_id"], current["evaluation_id"])
        history = (await self.client.get("/api/evaluations")).json()
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["evaluation_id"], old["evaluation_id"])
        self.assertNotEqual(history[0]["conversation"], history[1]["conversation"])
        response = await self.client.get("/api/submission/export", params={"group_id": "UNSEEN_TEST", "model_name": "test",
                "team_name": "Test", "model_mode": "external_api", "split": "prod"})
        self.assertEqual(response.status_code, 200, response.text)
        record = next(item for item in response.json()["results"] if item["conversation_id"] == "changing")
        self.assertEqual(record["status"], "ok")
        self.assertEqual(len(record["criteria"]), 8)

    async def test_failed_job_retains_observed_usage_on_idempotent_replay_and_restart(self):
        (await self.client.post("/api/datasets/import", json={"dataset": self.dataset(["failed"])})).raise_for_status()
        info = {"attempts": 2, "input_tokens": 200, "output_tokens": 40, "usage_complete": True,
                "estimated_cost": 0.000262, "attempt_details": [{"status": "http", "http_status": 503}]}
        self.reported_failure = EvaluatorError("Safe provider error", code="http", model_info=info)
        state = await self.submit("failed", key="failed-key")
        self.assertEqual(state["status"], "failed")
        self.assertEqual(state["error_code"], "http")
        self.assertEqual(state["model_info"], info)
        replay = await self.submit("failed", key="failed-key")
        self.assertEqual(replay, state)
        self.assertEqual(self.calls, ["failed"])
        await self.lifecycle.__aexit__(None, None, None)
        self.lifecycle = self.app.router.lifespan_context(self.app)
        await self.lifecycle.__aenter__()
        saved = (await self.client.get("/api/evaluation-jobs/" + state["job_id"])).json()
        self.assertEqual(saved, state)
        self.assertEqual((await self.client.get("/api/evaluations")).json(), [])


if __name__ == "__main__":
    unittest.main()
