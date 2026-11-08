"""Mechanical product-route checks only: these tests never call a model provider."""
from copy import deepcopy
from contextlib import redirect_stdout, redirect_stderr
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

import httpx
from fastapi.testclient import TestClient

from backend.cqa.main import create_app
from backend.cqa.models import EvaluationDraft
from scripts import control_run as runner


class ControlRunTests(unittest.TestCase):
    def setUp(self):
        directory = runner.ROOT / "data/runtime/rehearsal"
        directory.mkdir(parents=True, exist_ok=True)
        self.temporary = TemporaryDirectory(dir=directory)
        self.directory = Path(self.temporary.name)
        sample = json.loads((runner.ROOT / "fixtures/synthetic_dev.json").read_text(encoding="utf-8"))
        self.dataset = {"isSuccess": True, "error": "", "data": {
            "dataset_group_id": "REHEARSAL", "total_records": 10,
            "records": [{**deepcopy(sample["data"]["records"][0]), "conversation_id": f"test-{index}"} for index in range(10)]}}
        self.source = self.directory / "source.json"
        self.source.write_text(json.dumps(self.dataset), encoding="utf-8")
        self.calls = 0
        self.fail = False
        self.info = {"provider_configured": True, "provider": "test", "model": "mechanical-test",
                     "mode": "test", "prompt_version": "mechanical-v1"}
        self.app = create_app(self.directory / "test.sqlite3", evaluator=self.evaluate, evaluator_info=self.info)
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.args = runner.parser().parse_args(["--dataset", str(self.source), "--team-name", "Mechanical test only",
            "--state", str(self.directory / "state.json"), "--out", str(self.directory / "results.json"),
            "--allow-test-mode", "--poll-seconds", "0"])

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.temporary.cleanup()

    async def evaluate(self, conversation, checklist):
        self.calls += 1
        if self.fail and conversation.conversation_id == "test-0":
            raise RuntimeError("deliberate mechanical failure")
        message = next(m for m in conversation.transcript if m.speaker == "operator")
        results = [{"criterion_id": c.criterion_id, "status": "applicable",
            "answer": False if c.criterion_id == "critical" else c.scale_max if c.answer_type == "scale" else True,
            "explanation": "Mechanical fixture, not a quality judgement.",
            "evidence": [{"message_id": message.message_id, "quote": message.text}], "gross_error": False}
            for c in checklist.criteria]
        return SimpleNamespace(draft=EvaluationDraft(conversation_id=conversation.conversation_id,
            group_id=conversation.group_id, checklist_id=checklist.checklist_id, checklist_version=checklist.version,
            prompt_version="mechanical-v1", results=results), model_info={"provider": "test", "model": "mechanical-test"})

    def run_quietly(self, client=None):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return runner.run(self.args, client or self.client)

    def test_ten_product_jobs_export_and_resume_without_new_evaluations(self):
        self.assertEqual(self.run_quietly(), 0)
        self.assertEqual(self.calls, 10)
        state = runner._read(self.args.state)
        self.assertEqual(state["report"]["ok"], 10)
        self.assertEqual(len(runner._read(self.args.out)["results"]), 10)
        self.assertEqual(self.run_quietly(), 0)
        self.assertEqual(self.calls, 10)
        self.assertEqual(runner._read(self.args.state)["jobs"], state["jobs"])

    def test_reject_count_duplicate_voice_dev_and_unsuccessful(self):
        variants = []
        for mutation in (lambda d: d["data"].update(total_records=9),
                         lambda d: d["data"]["records"].pop(),
                         lambda d: d["data"]["records"][0].update(conversation_id="test-1"),
                         lambda d: d["data"]["records"][0].update(channel="voice call"),
                         lambda d: d["data"].update(dataset_group_id="DEV"),
                         lambda d: d.update(isSuccess=False)):
            value = deepcopy(self.dataset)
            mutation(value)
            variants.append(value)
        for value in variants:
            with self.subTest(value=value["data"]["total_records"]):
                self.source.write_text(json.dumps(value), encoding="utf-8")
                with self.assertRaises(runner.RunError):
                    runner.load_dataset(self.source, True)

    def test_source_order_can_differ_from_product_export_order(self):
        self.dataset["data"]["records"].reverse()
        self.source.write_text(json.dumps(self.dataset), encoding="utf-8")
        self.assertEqual(self.run_quietly(), 0)
        self.assertEqual(self.calls, 10)
        self.assertEqual(runner._read(self.args.state)["report"]["ok"], 10)
        output_ids = [record["conversation_id"] for record in runner._read(self.args.out)["results"]]
        self.assertNotEqual(output_ids, [record["conversation_id"] for record in self.dataset["data"]["records"]])

    def test_export_comparison_still_rejects_changed_content(self):
        underlying = self.client
        class ChangeExportRationale:
            def request(wrapper, method, path, **kwargs):
                response = underlying.request(method, path, **kwargs)
                if path == "/api/submission/export":
                    document = response.json()
                    document["results"][0]["criteria"][0]["rationale"] = "Changed after evaluation"
                    return httpx.Response(200, json=document)
                return response
        with self.assertRaisesRegex(runner.RunError, "differs from this run"):
            self.run_quietly(ChangeExportRationale())
        self.assertFalse(Path(self.args.out).exists())

    def test_changed_source_or_config_rejects_resume(self):
        self.assertEqual(self.run_quietly(), 0)
        self.dataset["data"]["records"][0]["transcript"][0]["text"] = "Changed source"
        self.source.write_text(json.dumps(self.dataset), encoding="utf-8")
        with self.assertRaisesRegex(runner.RunError, "Resume rejected"):
            self.run_quietly()
        self.assertEqual(self.calls, 10)

    def test_failure_stages_honest_error_nonzero_and_retry_is_explicit(self):
        self.fail = True
        self.assertEqual(self.run_quietly(), 2)
        self.assertEqual(runner._read(self.args.state)["report"]["errors"], 1)
        self.assertEqual(self.run_quietly(), 2)
        self.assertEqual(self.calls, 10)
        self.fail = False
        self.args.retry_failed = True
        self.assertEqual(self.run_quietly(), 0)
        self.assertEqual(self.calls, 11)

    def test_response_loss_reuses_persisted_idempotency_key(self):
        underlying = self.client
        class LoseFirstJobResponse:
            lost = False
            keys = []
            def request(wrapper, method, path, **kwargs):
                response = underlying.request(method, path, **kwargs)
                if method == "POST" and path == "/api/evaluation-jobs":
                    wrapper.keys.append(kwargs["json"]["idempotency_key"])
                    if not wrapper.lost:
                        wrapper.lost = True
                        raise httpx.ReadTimeout("response was lost")
                return response
        wrapper = LoseFirstJobResponse()
        self.assertEqual(self.run_quietly(wrapper), 0)
        self.assertEqual(self.calls, 10)
        self.assertEqual(wrapper.keys[0], wrapper.keys[1])

    def test_check_only_and_test_path_guard_never_dispatch(self):
        self.args.check_only = True
        self.assertEqual(self.run_quietly(), 0)
        self.assertEqual(self.calls, 0)
        self.assertFalse(Path(self.args.state).exists())
        self.args.out = str(runner.ROOT / "results/prod/results.json")
        with self.assertRaises(runner.RunError):
            self.run_quietly()

    def test_extra_existing_group_record_stops_before_paid_jobs(self):
        existing = deepcopy(self.dataset)
        existing["data"]["records"].append({**deepcopy(existing["data"]["records"][0]), "conversation_id": "extra"})
        existing["data"]["total_records"] = 11
        self.client.post("/api/datasets/import", json={"dataset": existing}).raise_for_status()
        with self.assertRaisesRegex(runner.RunError, "exactly these ten"):
            self.run_quietly()
        self.assertEqual(self.calls, 0)


if __name__ == "__main__":
    unittest.main()
