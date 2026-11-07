from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from backend.cqa.evaluator.service import EvaluationRun, EvaluatorError, Settings
from backend.cqa.importer import normalize
from backend.cqa.models import ChecklistVersion, DatasetExport, EvaluationDraft
from evaluation.benchmark import (
    aggregate_report, checkpoint_for, execute_plan, fingerprint, load_independent_labels,
    make_plan, parser, request_payload, reserve_cost, select_conversations,
    validate_label_snapshots, write_json, write_reports,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


def fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class SelectionAndFingerprintTests(unittest.TestCase):
    def test_sampling_is_stable_balanced_and_group_aware(self):
        source = normalize(DatasetExport.model_validate(fixture("synthetic_dev.json")))
        expanded = []
        for group in ("B", "A"):
            for index in range(3):
                for conversation in source:
                    copy = conversation.model_copy(deep=True)
                    copy.group_id = group
                    copy.conversation_id += str(index)
                    expanded.append(copy)
        a = select_conversations(expanded, per_language=2, seed=2026, channel="all")
        b = select_conversations(list(reversed(expanded)), per_language=2, seed=2026, channel="all")
        self.assertEqual([c.model_dump() for c in a], [c.model_dump() for c in b])
        for language in ("RU", "KZ", "EN"):
            self.assertEqual({c.group_id for c in a if c.language == language}, {"A", "B"})

    def test_resume_rejects_source_and_configuration_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary)
            manifest = {"fingerprint": fingerprint({"source": "v1", "model": "m", "effort": "max"})}
            write_json(out / "manifest.json", manifest)
            checkpoint = checkpoint_for(out, manifest, resume=True)
            self.assertEqual(checkpoint["records"], {})
            write_json(out / "checkpoint.json", checkpoint)
            self.assertEqual(checkpoint_for(out, manifest, resume=True), checkpoint)
            for changed in ({"source": "v2", "model": "m", "effort": "max"},
                            {"source": "v1", "model": "m", "effort": "low"}):
                with self.assertRaisesRegex(ValueError, "fingerprint mismatch"):
                    checkpoint_for(out, {"fingerprint": fingerprint(changed)}, resume=True)

    def test_unknown_prices_cannot_be_assumed_free_and_bound_covers_retries(self):
        settings = Settings("https://api.openai.com/v1", "gpt-6-astra", max_tokens=16384,
                            api_style="responses", reasoning_effort="max")
        payload = {"model": "gpt-6-astra", "input": "Қазақша Русский English"}
        amount, tokens = reserve_cost(payload, settings, {"input_per_million": 10, "cache_write_input_per_million": 12.5, "output_per_million": 50})
        self.assertGreater(tokens, 4096)
        self.assertAlmostEqual(amount, 2 * (tokens * 12.5 + 16384 * 50) / 1_000_000)
        self.assertIsNone(reserve_cost(payload, settings, None)[0])

    def test_plan_keeps_max_effort_and_no_credential_in_manifest(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict("os.environ", {"LLM_API_KEY": "benchmark-test-private-key"}):
            args = parser().parse_args(["--dataset", str(FIXTURES / "synthetic_dev.json"),
                                       "--checklist", str(FIXTURES / "checklist.example.json"),
                                       "--models", "gpt-6.1-sol", "gpt-6-astra", "gpt-6-luna", "--reasoning-effort", "max",
                                       "--out", temporary, "--channel", "all", "--per-language", "1", "--dry-run"])
            manifest, tasks, configs, _ = make_plan(args)
            self.assertEqual(manifest["language_counts"], {"RU": 1, "KZ": 1, "EN": 1})
            self.assertEqual(len(tasks), 9)
            self.assertNotIn("benchmark-test-private-key", json.dumps(manifest))
            self.assertTrue(all(s.reasoning_effort == "max" and s.max_tokens == 16384 for s in configs.values()))

    def test_default_plan_only_selects_economical_luna_without_paid_dispatch(self):
        args = parser().parse_args(["--dataset", str(FIXTURES / "synthetic_dev.json"),
                                    "--out", "unused-dry-run", "--dry-run"])
        self.assertEqual(args.models, ["gpt-6-luna"])
        self.assertEqual(args.reasoning_effort, "high")
        self.assertEqual(args.timeout_seconds, 180)
        self.assertIsNone(args.budget_usd)

    def test_labels_need_independent_human_attestation(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "labels.json"
            write_json(path, {"items": [], "provenance": "model_generated"})
            with self.assertRaisesRegex(ValueError, "independent_human_review"):
                load_independent_labels(str(path), "all")


class BenchmarkExecutionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.conversation = normalize(DatasetExport.model_validate(fixture("synthetic_dev.json")))[0]
        self.checklist = ChecklistVersion.model_validate(fixture("checklist.example.json"))
        self.draft = EvaluationDraft(conversation_id=self.conversation.conversation_id, group_id=self.conversation.group_id,
                                    checklist_id=self.checklist.checklist_id, checklist_version=self.checklist.version,
                                    prompt_version="test", results=fixture("evaluation.example.json")["results"])
        self.settings = Settings("https://api.openai.com/v1", "gpt-6-luna", max_tokens=16384,
                                 api_style="responses", reasoning_effort="max")
        self.tasks = []
        for index in range(2):
            conversation = self.conversation.model_dump(mode="json")
            conversation["conversation_id"] += str(index)
            self.tasks.append({"task_id": str(index), "model": "gpt-6-luna", "conversation": conversation,
                               "request_sha256": fingerprint(conversation), "reservation_usd": 1.0})
        self.manifest = {"fingerprint": "fixed", "inputs": {"models": {"gpt-6-luna": {}},
                         "pricing": {"verified_at": "2026-11-07", "models": {}}},
                         "tasks": self.tasks, "language_counts": {"RU": 2}}
        self.checkpoint = {"fingerprint": "fixed", "records": {}}

    async def execute(self, out, *, budget=2, concurrency=1):
        return await execute_plan(self.manifest, self.tasks, {"gpt-6-luna": self.settings}, self.checklist,
                                  Path(out), self.checkpoint, budget_usd=budget, concurrency=concurrency)

    def result(self, **overrides):
        info = {"usage_complete": True, "estimated_cost": 0.2, "attempts": 2,
                "input_tokens": 200, "output_tokens": 100, "reasoning_tokens": 80}
        info.update(overrides)
        return EvaluationRun(self.draft, info)

    async def test_retries_counted_and_resume_skips_completed_requests(self):
        with tempfile.TemporaryDirectory() as temporary, patch("evaluation.benchmark.evaluate", new=AsyncMock(return_value=self.result())) as evaluate:
            checkpoint = await self.execute(temporary, budget=1.3)
            self.assertEqual(evaluate.await_count, 2)  # Actual cost releases capacity for the second task.
            self.assertAlmostEqual(checkpoint["charged_or_reserved_usd"], 0.4)
            await self.execute(temporary, budget=1.3)
            self.assertEqual(evaluate.await_count, 2)
            report = write_reports(Path(temporary), self.manifest, checkpoint, labels=None, split="all")
            row = report["models"][0]
            self.assertEqual(row["retries"], 2)
            self.assertAlmostEqual(row["total_cost_usd"], 0.4)
            self.assertEqual(row["tokens_observed"]["reasoning_tokens"], 160)
            self.assertNotIn("human_quality", row)
            self.assertIn("no_ground_truth", report["metric_status"])
            export = json.loads(next((Path(temporary) / "predictions").glob("*.json")).read_text(encoding="utf-8"))
            self.assertEqual(export["evaluations"][0]["original_results"], export["evaluations"][0]["results"])

    async def test_unknown_usage_retains_reservation_and_does_not_report_zero_cost(self):
        result = self.result(usage_complete=False, estimated_cost=None, known_estimated_cost=0.03)
        with tempfile.TemporaryDirectory() as temporary, patch("evaluation.benchmark.evaluate", new=AsyncMock(return_value=result)) as evaluate:
            checkpoint = await self.execute(temporary, budget=1.3)
            self.assertEqual(evaluate.await_count, 1)
            self.assertEqual(checkpoint["records"]["1"]["status"], "budget_skipped")
            self.assertEqual(checkpoint["charged_or_reserved_usd"], 1)
            row = aggregate_report(self.manifest, checkpoint)["models"][0]
            self.assertIsNone(row["total_cost_usd"])
            self.assertEqual(row["unknown_cost_requests"], 1)
            self.assertEqual(row["known_cost_usd"], 0.03)

    async def test_budget_blocks_dispatch_before_spending(self):
        with tempfile.TemporaryDirectory() as temporary, patch("evaluation.benchmark.evaluate", new=AsyncMock()) as evaluate:
            checkpoint = await self.execute(temporary, budget=0.9)
            evaluate.assert_not_awaited()
            self.assertTrue(all(r["status"] == "budget_skipped" for r in checkpoint["records"].values()))

    async def test_auth_failure_stops_further_requests(self):
        error = EvaluatorError("Safe authorization failure", code="auth", model_info={"attempts": 1, "usage_complete": False})
        with tempfile.TemporaryDirectory() as temporary, patch("evaluation.benchmark.evaluate", new=AsyncMock(side_effect=error)) as evaluate:
            checkpoint = await self.execute(temporary, budget=3, concurrency=4)
            self.assertEqual(evaluate.await_count, 1)
            self.assertEqual(checkpoint["records"]["1"]["status"], "configuration_skipped")

    async def test_interrupted_dispatch_is_not_repeated_or_forgotten(self):
        task = self.tasks[0]
        self.checkpoint["records"]["0"] = {**task, "status": "running", "budget_charge_usd": 1.0,
                                             "started_at": "2026-11-07T00:00:00Z", "model_info": {}}
        with tempfile.TemporaryDirectory() as temporary, patch("evaluation.benchmark.evaluate", new=AsyncMock()) as evaluate:
            checkpoint = await self.execute(temporary, budget=1.3)
            evaluate.assert_not_awaited()
            self.assertEqual(checkpoint["records"]["0"]["status"], "interrupted_unknown")
            self.assertEqual(checkpoint["charged_or_reserved_usd"], 1.0)

    def test_label_snapshot_mismatch_is_rejected_before_dispatch(self):
        labels = {"checklist": self.checklist.model_dump(mode="json"),
                  "items": [{"conversation": deepcopy(self.tasks[0]["conversation"])}]}
        labels["items"][0]["conversation"]["transcript"][0]["text"] = "changed"
        with self.assertRaisesRegex(ValueError, "different conversation"):
            validate_label_snapshots(labels, self.tasks, self.checklist)


if __name__ == "__main__":
    unittest.main()
