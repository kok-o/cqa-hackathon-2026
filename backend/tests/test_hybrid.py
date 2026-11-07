from copy import deepcopy
from contextlib import redirect_stderr
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import AsyncMock, patch

from backend.cqa.evaluator.service import EvaluationRun, EvaluatorError
from backend.cqa.evaluator.validation import validate_draft
from backend.cqa.importer import normalize
from backend.cqa.models import ChecklistVersion, CriterionResult, DatasetExport, EvaluationDraft
from backend.cqa.scoring import calculate_score
from evaluation import benchmark, hybrid

ROOT = Path(__file__).resolve().parents[2]
CANARY = "TEST_SECRET_HYBRID_CREDENTIAL"


class HybridTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.checklist = ChecklistVersion.model_validate(benchmark.read_json(ROOT / "fixtures/checklist.official.json"))
        self.prices = benchmark.load_prices(ROOT / "evaluation/model_profiles.json")
        self.dataset = benchmark.read_json(ROOT / "fixtures/synthetic_dev.json")
        record = deepcopy(self.dataset["data"]["records"][0])
        record["conversation_id"] = "hybrid-test"
        record["transcript"] = [
            {"speaker": "client", "timestamp": "00:00", "text": "Please explain the public help menu."},
            {"speaker": "operator", "timestamp": "00:01", "text": "Hello, I am Alex. Open Help, then FAQ. Anything else? Goodbye."},
        ]
        self.dataset["data"] = {"dataset_group_id": "HYBRID_TEST", "total_records": 1, "records": [record]}
        self.source = normalize(DatasetExport.model_validate(self.dataset))[0]
        self.manifest = hybrid.make_manifest(self.dataset, self.checklist, self.prices, expected_sha256="a" * 64)
        self.checkpoint = {"fingerprint": self.manifest["fingerprint"], "records": {}}

    def draft(self, source=None, checklist=None, changes=None):
        source, checklist = source or self.source, checklist or self.checklist
        operator = next(m for m in source.transcript if m.speaker == "operator")
        results = []
        for criterion in checklist.criteria:
            answer = criterion.scale_max if criterion.answer_type == "scale" else criterion.positive_answer
            value = {"criterion_id": criterion.criterion_id, "status": "applicable", "answer": answer,
                     "explanation": "Synthetic output used only to test runner mechanics.",
                     "evidence": [{"message_id": operator.message_id, "quote": operator.text}], "gross_error": False}
            value.update((changes or {}).get(criterion.criterion_id, {}))
            results.append(CriterionResult.model_validate(value))
        return EvaluationDraft(conversation_id=source.conversation_id, group_id=source.group_id,
            checklist_id=checklist.checklist_id, checklist_version=checklist.version,
            prompt_version="test", results=results)

    def info(self, model):
        cost = .001 if model == hybrid.LUNA else .002
        attempt = {"input_tokens": 1000, "output_tokens": 100, "reasoning_tokens": 80,
                   "usage_complete": True, "response_service_tier": "default", "estimated_cost": cost}
        return {"requested_model": model, "model": model, "prompt_version": "test", "attempts": 1,
                "usage_complete": True, "estimated_cost": cost, "known_estimated_cost": cost,
                "attempt_details": [attempt], "input_tokens": 1000, "output_tokens": 100}

    async def evaluated(self, source, checklist, *, settings):
        changes = {"consultation": {"answer": 10}} if settings.model == hybrid.LUNA else {}
        return EvaluationRun(self.draft(source, checklist, changes), self.info(settings.model))

    async def execute(self, out, *, budget=3):
        return await hybrid.execute(self.manifest, self.checkpoint, Path(out), api_key=CANARY, budget_usd=budget)

    def test_frozen_policy_and_profiles_do_not_contain_credentials(self):
        self.assertEqual(benchmark.fingerprint(hybrid.ROUTING_POLICY), "9b5bc627ae53a9e11873bb06cad22830afd07fb48f4f75f842fc2582cba9a4e9")
        settings = hybrid.model_settings(self.prices, api_key=CANARY)
        self.assertEqual((settings[hybrid.LUNA].reasoning_effort, settings[hybrid.LUNA].max_tokens, settings[hybrid.LUNA].timeout_seconds), ("high", 16384, 180))
        self.assertEqual((settings[hybrid.SOL].reasoning_effort, settings[hybrid.SOL].max_tokens, settings[hybrid.SOL].timeout_seconds), ("max", 32768, 600))
        self.assertNotIn(CANARY, json.dumps(self.manifest))
        self.assertNotIn("api_key", json.dumps(self.manifest))

    def test_route_ignores_na_easy_failures_explanations_and_client_markers(self):
        source = self.source.model_copy(deep=True)
        source.transcript[0].text += " balance portfolio passport card number қалдық ИИН"
        changes = {key: {"status": "not_applicable", "answer": None} for key in ("discovery", "identification", "closure")}
        changes["contact"] = {"answer": False}
        changes["critical"] = {"explanation": "Uncertain balance account code personal; no confidence signal is used."}
        self.assertEqual(hybrid.route_criteria(source, self.checklist, self.draft(source, changes=changes))["criterion_ids"], [])

    def test_route_pairs_risks_and_selects_only_declared_structural_signals(self):
        changes = {"identification": {"answer": False}, "consultation": {"answer": 15},
                   "dialogue": {"answer": 7}, "empathy": {"answer": False},
                   "closure": {"status": "needs_review", "answer": None}}
        route = hybrid.route_criteria(self.source, self.checklist, self.draft(changes=changes))
        self.assertEqual(route["criterion_ids"], ["identification", "consultation", "empathy", "dialogue", "closure", "critical"])
        for text in ("Ваш баланс указан в приложении.", "Your portfolio is visible.", "ЖСН тексерілді.",
                     "Please transfer to my personal card.", "Переведите на мою карту.", "Менің картама төлеңіз."):
            with self.subTest(text=text):
                source = self.source.model_copy(deep=True)
                source.transcript[1].text = text
                self.assertEqual(hybrid.route_criteria(source, self.checklist, self.draft(source))["criterion_ids"], ["identification", "critical"])

    def test_reviewer_gets_full_rubric_without_luna_answers_and_combination_is_immutable(self):
        luna = self.draft(changes={"identification": {"answer": False, "explanation": "LUNA_ANSWER_CANARY"}})
        before = luna.model_dump(mode="json")
        scoped = hybrid.review_checklist(self.checklist, ["identification", "critical"])
        payload = benchmark.request_payload(hybrid.model_settings(self.prices)[hybrid.SOL], self.source, scoped)
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertIn("reference_only_full_official_rubric", serialized)
        self.assertIn(self.checklist.criteria[3].instruction, json.loads(payload["input"][1]["content"])["checklist"]["criteria"][0]["instruction"])
        self.assertNotIn("LUNA_ANSWER_CANARY", serialized)
        reviewer = self.draft(checklist=scoped, changes={"identification": {"status": "not_applicable", "answer": None}})
        merged = hybrid.combine_drafts(self.source, self.checklist, luna, reviewer, ["identification", "critical"])
        self.assertEqual(luna.model_dump(mode="json"), before)
        self.assertEqual(merged.results[2].status, "not_applicable")
        self.assertEqual(merged.results[0], luna.results[0])
        self.assertEqual(calculate_score(self.checklist, merged.results)["total"], 100)
        bad = reviewer.model_copy(deep=True)
        bad.results[0].evidence[0].quote = "TEST_SECRET_FABRICATED_QUOTE"
        with self.assertRaisesRegex(ValueError, "exact source quote") as caught:
            hybrid.combine_drafts(self.source, self.checklist, luna, bad, ["identification", "critical"])
        self.assertNotIn("TEST_SECRET_FABRICATED_QUOTE", str(caught.exception))
        with self.assertRaises(ValueError):
            hybrid.combine_drafts(self.source, self.checklist, luna, self.draft(), ["identification", "critical"])

    async def test_shared_baseline_subset_review_combined_cost_and_resume(self):
        with TemporaryDirectory() as temporary, patch("evaluation.benchmark.evaluate", new=AsyncMock(side_effect=self.evaluated)) as evaluate:
            pairs = await self.execute(temporary)
            self.assertEqual(evaluate.await_count, 2)
            self.assertEqual(len(evaluate.await_args_list[0].args[1].criteria), 8)
            self.assertEqual([c.criterion_id for c in evaluate.await_args_list[1].args[1].criteria], ["consultation"])
            pair = pairs["pairs"][0]
            self.assertEqual(pair["luna_original_results"][3]["answer"], 10)
            self.assertEqual(pair["hybrid_results"][3]["answer"], 20)
            self.assertEqual(pair["review_status"], "completed")
            self.assertAlmostEqual(self.checkpoint["charged_or_reserved_usd"], .003)
            persisted = benchmark.read_json(Path(temporary) / "checkpoint.json")
            self.assertNotIn(CANARY, json.dumps(persisted))
            self.assertNotIn(CANARY, (Path(temporary) / "paired.json").read_text(encoding="utf-8"))
            self.checkpoint = persisted
            self.assertEqual(await self.execute(temporary), pairs)
            self.assertEqual(evaluate.await_count, 2, "Resume must not repeat either paid stage")

    async def test_sol_failure_preserves_luna_and_unknown_attempt_reservation(self):
        async def failing(source, checklist, *, settings):
            if settings.model == hybrid.SOL:
                raise EvaluatorError("Safe provider timeout", code="timeout", model_info={"attempts": 2,
                    "usage_complete": False, "estimated_cost": None, "known_estimated_cost": .007})
            return await self.evaluated(source, checklist, settings=settings)
        with TemporaryDirectory() as temporary, patch("evaluation.benchmark.evaluate", new=AsyncMock(side_effect=failing)):
            pair = (await self.execute(temporary))["pairs"][0]
            self.assertTrue(pair["fallback_to_luna"])
            self.assertEqual(pair["hybrid_results"], pair["luna_original_results"])
            self.assertEqual(pair["hybrid_score"], pair["luna_score"])
            review = next(r for r in self.checkpoint["records"].values() if r["model"] == hybrid.SOL)
            self.assertEqual(review["budget_charge_usd"], review["reservation_usd"])
            cost = hybrid.cost_summary(list(self.checkpoint["records"].values()), self.prices["models"])
            self.assertAlmostEqual(cost["known_cost_usd"], .008)
            self.assertEqual(cost["unknown_cost_requests"], 1)
            self.assertIsNone(cost["total_cost_usd"])
            self.assertIsNone(cost["full_cold_estimate_usd"])

    async def test_budget_cap_skips_reviewer_and_rejects_larger_total(self):
        with TemporaryDirectory() as temporary, patch("evaluation.benchmark.evaluate", new=AsyncMock(side_effect=self.evaluated)) as evaluate:
            pair = (await self.execute(temporary, budget=.05))["pairs"][0]
            self.assertEqual(evaluate.await_count, 1)
            self.assertEqual(pair["review_status"], "budget_skipped")
            self.assertTrue(pair["fallback_to_luna"])
            with self.assertRaisesRegex(ValueError, "budget"):
                await self.execute(temporary, budget=3.01)

    async def test_fatal_reviewer_stops_other_scope_and_resume_never_dispatches(self):
        second = deepcopy(self.dataset['data']['records'][0])
        second['conversation_id'] += '-second'
        self.dataset['data']['records'].append(second)
        self.dataset['data']['total_records'] = 2
        self.manifest = hybrid.make_manifest(self.dataset, self.checklist, self.prices, expected_sha256='a' * 64)
        for code in ('auth', 'request', 'reservation_exceeded'):
            with self.subTest(code=code):
                self.checkpoint = {'fingerprint': self.manifest['fingerprint'], 'records': {}}
                reviewer_calls = 0
                async def fatal(source, checklist, *, settings):
                    nonlocal reviewer_calls
                    if settings.model == hybrid.LUNA:
                        changes = {'empathy': {'answer': False}} if source.conversation_id.endswith('second') else {'consultation': {'answer': 10}}
                        return EvaluationRun(self.draft(source, checklist, changes), self.info(settings.model))
                    reviewer_calls += 1
                    if code == 'reservation_exceeded':
                        info = self.info(settings.model)
                        info['estimated_cost'] = info['known_estimated_cost'] = 1.5
                        return EvaluationRun(self.draft(source, checklist), info)
                    raise EvaluatorError('Safe fatal provider error', code=code,
                                         model_info={'attempts': 1, 'usage_complete': False, 'estimated_cost': None})
                with TemporaryDirectory() as temporary, patch('evaluation.benchmark.evaluate', new=AsyncMock(side_effect=fatal)) as evaluate:
                    pairs = (await self.execute(temporary))['pairs']
                    self.assertEqual(reviewer_calls, 1)
                    self.assertEqual(pairs[1]['review_status'], 'configuration_skipped')
                    self.assertTrue(pairs[1]['fallback_to_luna'])
                    total_calls = evaluate.await_count
                    self.checkpoint = benchmark.read_json(Path(temporary) / 'checkpoint.json')
                    await self.execute(temporary)
                    self.assertEqual(evaluate.await_count, total_calls)
                    self.assertEqual(reviewer_calls, 1)

    async def test_interrupted_review_never_repeats_and_tampered_resume_rejected_before_dispatch(self):
        with TemporaryDirectory() as temporary, patch("evaluation.benchmark.evaluate", new=AsyncMock(side_effect=self.evaluated)) as evaluate:
            await self.execute(temporary)
            reviewer = next(r for r in self.checkpoint["records"].values() if r["model"] == hybrid.SOL)
            reviewer.update(status="running", model_info={}, actual_cost_usd=None, budget_charge_usd=reviewer["reservation_usd"])
            pair = (await self.execute(temporary))["pairs"][0]
            self.assertEqual(pair["review_status"], "interrupted_unknown")
            self.assertTrue(pair["fallback_to_luna"])
            self.assertEqual(evaluate.await_count, 2)
            reviewer["budget_charge_usd"] = 0
            with self.assertRaisesRegex(ValueError, "budget charge"):
                await self.execute(temporary)
            self.assertEqual(evaluate.await_count, 2)

    def test_code_policy_scope_and_checkpoint_snapshot_frozen(self):
        bad = deepcopy(self.manifest)
        bad["inputs"]["policy"]["consultation_values"] = [20]
        with self.assertRaisesRegex(ValueError, "Frozen"):
            hybrid.verify_manifest(bad)
        bad = deepcopy(self.manifest)
        bad["tasks"][0]["conversation"]["transcript"][0]["text"] = "changed"
        with self.assertRaisesRegex(ValueError, "Frozen"):
            hybrid.verify_manifest(bad)
        with patch("evaluation.hybrid.code_hashes", return_value={}):
            with self.assertRaisesRegex(ValueError, "Frozen"):
                hybrid.verify_manifest(self.manifest)

    def test_process_lock_prevents_concurrent_resume_and_releases(self):
        with TemporaryDirectory() as temporary:
            with hybrid.run_lock(Path(temporary)):
                with self.assertRaisesRegex(ValueError, "Another process"):
                    with hybrid.run_lock(Path(temporary)):
                        self.fail("Concurrent run acquired the lock")
            with hybrid.run_lock(Path(temporary)):
                pass

    def test_cli_unexpected_failure_does_not_log_credential_or_provider_text(self):
        from scripts.hybrid_benchmark import main
        output = io.StringIO()
        with TemporaryDirectory() as temporary:
            benchmark.write_json(Path(temporary) / 'manifest.json', self.manifest)
            benchmark.write_json(Path(temporary) / 'checkpoint.json', self.checkpoint)
            with patch('scripts.hybrid_benchmark.load_dotenv'), patch.dict('os.environ', {'LLM_API_KEY': CANARY}), \
                    patch('scripts.hybrid_benchmark.hybrid.execute', new=AsyncMock(side_effect=RuntimeError(CANARY))), redirect_stderr(output):
                self.assertEqual(main(['live', '--out', temporary, '--budget-usd', '3']), 2)
        self.assertNotIn(CANARY, output.getvalue())
        self.assertIn('failed validation or configuration', output.getvalue())

    def test_cold_cost_counts_output_reasoning_once_and_rejects_unknown_tier(self):
        record = {"model_info": self.info(hybrid.SOL), "budget_charge_usd": .002}
        self.assertAlmostEqual(hybrid.cold_cost(record, self.prices["models"][hybrid.SOL]), .0035)
        record["model_info"]["attempt_details"][0]["response_service_tier"] = "priority"
        self.assertIsNone(hybrid.cold_cost(record, self.prices["models"][hybrid.SOL]))

    def test_missing_or_unresolved_score_does_not_count_as_complete_pair(self):
        complete = {"completed": True, "luna_score": {"status": "complete", "total": 90},
                    "hybrid_score": {"status": "complete", "total": 100}}
        self.assertTrue(hybrid.has_complete_scores(complete))
        for replacement in (None, {}, {"status": "incomplete", "total": None}, {"status": "complete", "total": None}):
            with self.subTest(score=replacement):
                self.assertFalse(hybrid.has_complete_scores({**complete, "luna_score": replacement}))
                self.assertFalse(hybrid.has_complete_scores({**complete, "hybrid_score": replacement}))

    async def test_report_all_criteria_na_score_mae_and_failed_reviews_close_gate(self):
        with TemporaryDirectory() as temporary, patch("evaluation.benchmark.evaluate", new=AsyncMock(side_effect=self.evaluated)):
            await self.execute(temporary)
        expected_draft = self.draft()
        expected = {"provenance": "synthetic_regression_authored_by_assistant", "not_ground_truth": True,
                    "dataset_sha256": self.manifest["inputs"]["dataset_sha256"],
                    "checklist_sha256": self.manifest["inputs"]["checklist_sha256"],
                    "items": [{"conversation_id": self.source.conversation_id, "group_id": self.source.group_id,
                               "family": "unit_fixture",
                               "expected_results": [r.model_dump(mode="json") for r in expected_draft.results]}]}
        report = hybrid.comparison_report(self.manifest, self.checkpoint, expected)
        self.assertEqual(report["checked_decisions"], 8)
        self.assertEqual(report["corrections"], 1)
        self.assertEqual(report["regressions"], 0)
        self.assertEqual(report["hybrid_score_mae"], 0)
        self.assertGreater(report["baseline_score_mae"], 0)
        self.assertFalse(report["gate"]["enable_hybrid"], "One synthetic fixture is not the preregistered 24-case pilot")
        self.assertAlmostEqual(report["costs"]["hybrid"]["total_cost_usd"], .003)
        self.assertEqual(report["criterion_routing_rate"], 1 / 8)

        # A schema-valid needs_review answer has no usable total. Even if Sol
        # resolves it, the predeclared paired-completeness condition stays shut.
        self.checkpoint = {"fingerprint": self.manifest["fingerprint"], "records": {}}
        async def unresolved(source, checklist, *, settings):
            changes = {"consultation": {"status": "needs_review", "answer": None}} if settings.model == hybrid.LUNA else {}
            return EvaluationRun(self.draft(source, checklist, changes), self.info(settings.model))
        with TemporaryDirectory() as temporary, patch("evaluation.benchmark.evaluate", new=AsyncMock(side_effect=unresolved)):
            await self.execute(temporary)
        report = hybrid.comparison_report(self.manifest, self.checkpoint, expected)
        self.assertTrue(report["cases"][0]["validated_draft"])
        self.assertFalse(report["cases"][0]["completed"])
        self.assertFalse(report["gate"]["conditions"]["complete_unique_24_by_8"])


if __name__ == "__main__":
    unittest.main()
