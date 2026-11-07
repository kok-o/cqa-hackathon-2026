from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import httpx

from backend.cqa.evaluator.service import (
    EvaluatorError, Settings, build_messages, evaluate, get_evaluator_info,
)
from backend.cqa.evaluator.validation import validate_draft
from backend.cqa.importer import normalize
from backend.cqa.models import ChecklistVersion, Criterion, DatasetExport, EvaluationDraft
from evaluation.metrics import summarize
from evaluation.cli import validate_labels

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


def fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def response(results, **overrides):
    body = {"choices": [{"message": {"content": json.dumps({"results": results}, ensure_ascii=False)},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 40}}
    body.update(overrides)
    return httpx.Response(200, json=body)


class EvaluatorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.conversation = normalize(DatasetExport.model_validate(fixture("synthetic_dev.json")))[0]
        self.checklist = ChecklistVersion.model_validate(fixture("checklist.example.json"))
        self.results = fixture("evaluation.example.json")["results"]
        self.settings = Settings("http://localhost:11434/v1", "test-only", api_key="secret-for-test")

    async def call(self, handler, **kwargs):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await evaluate(self.conversation, kwargs.pop("checklist", self.checklist),
                                  settings=kwargs.pop("settings", self.settings), client=client, **kwargs)

    async def test_dynamic_input_schema_usage_and_server_owned_ids(self):
        captured = []
        def handler(request):
            captured.append(json.loads(request.content))
            self.assertEqual(str(request.url), "http://localhost:11434/v1/chat/completions")
            self.assertEqual(request.headers["authorization"], "Bearer secret-for-test")
            return response(list(reversed(self.results)))
        run = await self.call(handler)
        self.assertEqual(run.draft.conversation_id, self.conversation.conversation_id)
        self.assertEqual([r.criterion_id for r in run.draft.results], [c.criterion_id for c in self.checklist.criteria])
        payload = captured[0]
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        data = json.loads(payload["messages"][1]["content"])
        self.assertEqual(data["checklist"]["criteria"][1]["scale_max"], 2)
        self.assertNotIn("audio_url", data["conversation"])
        self.assertEqual(run.model_info["total_tokens"], 140)
        self.assertEqual(run.model_info["attempts"], 1)
        self.assertNotIn("secret-for-test", json.dumps(run.model_info))
        self.assertIsNone(run.model_info["estimated_cost"])

    async def test_added_criterion_is_sent_and_required(self):
        checklist = self.checklist.model_copy(deep=True)
        checklist.version = 2
        checklist.criteria.append(Criterion(criterion_id="new-choice", title="Новый критерий",
            instruction="Есть ли понятное действие? Выберите clear или unclear.", weight=3,
            answer_type="choice", choices={"clear": 1, "unclear": 0}))
        new_result = deepcopy(self.results[1])
        new_result.update(criterion_id="new-choice", answer="clear")
        def handler(request):
            data = json.loads(json.loads(request.content)["messages"][1]["content"])
            self.assertEqual(data["checklist"]["version"], 2)
            self.assertEqual(data["checklist"]["criteria"][2]["instruction"], checklist.criteria[2].instruction)
            return response(self.results + [new_result])
        run = await self.call(handler, checklist=checklist)
        self.assertEqual(run.draft.results[-1].answer, "clear")

    async def test_invalid_quote_is_retried_and_all_usage_counted(self):
        invalid = deepcopy(self.results)
        invalid[0]["evidence"][0]["quote"] = "never spoken"
        calls = 0
        def handler(request):
            nonlocal calls
            calls += 1
            return response(invalid if calls == 1 else self.results)
        settings = Settings("http://localhost:11434/v1", "test-only", input_cost_per_million=2,
                            output_cost_per_million=3)
        run = await self.call(handler, settings=settings)
        self.assertEqual(calls, 2)
        self.assertEqual(run.model_info["prompt_tokens"], 200)
        self.assertEqual(run.model_info["completion_tokens"], 80)
        self.assertAlmostEqual(run.model_info["estimated_cost"], 0.00064)

    async def test_fabricated_quotes_fail_without_fallback(self):
        invalid = deepcopy(self.results)
        invalid[0]["evidence"][0]["quote"] = "invented"
        with self.assertRaisesRegex(EvaluatorError, "дважды"):
            await self.call(lambda request: response(invalid))

    async def test_answer_type_is_not_coerced(self):
        invalid = deepcopy(self.results)
        invalid[0]["answer"] = "true"
        with self.assertRaises(EvaluatorError):
            await self.call(lambda request: response(invalid))

    async def test_refusal_is_visible_and_not_retried(self):
        calls = 0
        def handler(request):
            nonlocal calls
            calls += 1
            return response([], choices=[{"message": {"refusal": "unsafe", "content": None}}])
        with self.assertRaisesRegex(EvaluatorError, "отказалась"):
            await self.call(handler)
        self.assertEqual(calls, 1)

    async def test_provider_error_never_leaks_response_or_key(self):
        with self.assertRaises(EvaluatorError) as caught:
            await self.call(lambda request: httpx.Response(401, text="secret-for-test request rejected"))
        self.assertNotIn("secret-for-test", str(caught.exception))

    async def test_timeout_retried_once_then_visible(self):
        calls = 0
        def handler(request):
            nonlocal calls
            calls += 1
            raise httpx.ReadTimeout("sensitive URL", request=request)
        with self.assertRaisesRegex(EvaluatorError, "вовремя"):
            await self.call(handler)
        self.assertEqual(calls, 2)

    async def test_json_schema_mode_and_no_format_mode(self):
        for mode in ("json_schema", "none"):
            with self.subTest(mode=mode):
                def handler(request):
                    payload = json.loads(request.content)
                    if mode == "json_schema":
                        schema = payload["response_format"]["json_schema"]
                        self.assertTrue(schema["strict"])
                        self.assertFalse(schema["schema"]["additionalProperties"])
                    else:
                        self.assertNotIn("response_format", payload)
                    return response(self.results)
                await self.call(handler, settings=Settings("http://localhost:11434/v1", "test-only", json_mode=mode))

    async def test_audio_criteria_not_sent_or_guessed(self):
        checklist = self.checklist.model_copy(deep=True)
        checklist.criteria[1].requires_audio = True
        def handler(request):
            data = json.loads(json.loads(request.content)["messages"][1]["content"])
            self.assertEqual(len(data["checklist"]["criteria"]), 1)
            return response(self.results[:1])
        run = await self.call(handler, checklist=checklist)
        self.assertEqual(run.draft.results[1].status, "needs_review")
        self.assertIsNone(run.draft.results[1].answer)
        self.assertFalse(run.model_info["audio_processed"])

    async def test_only_audio_needs_no_call_and_is_incomplete(self):
        checklist = self.checklist.model_copy(deep=True)
        for criterion in checklist.criteria:
            criterion.requires_audio = True
        def handler(request):
            self.fail("Audio-only text evaluator must not call a model")
        run = await self.call(handler, checklist=checklist)
        self.assertTrue(all(r.status == "needs_review" for r in run.draft.results))
        self.assertEqual(run.model_info["attempts"], 0)

    async def test_unknown_usage_cannot_produce_cost(self):
        calls = 0
        def handler(request):
            nonlocal calls
            calls += 1
            return httpx.Response(200, text="not json") if calls == 1 else response(self.results)
        settings = Settings("http://localhost:11434/v1", "test-only", input_cost_per_million=1,
                            output_cost_per_million=1)
        run = await self.call(handler, settings=settings)
        self.assertFalse(run.model_info["usage_complete"])
        self.assertIsNone(run.model_info["estimated_cost"])

    def test_prompt_separates_operator_and_untrusted_transcript(self):
        messages = build_messages(self.conversation, self.checklist)
        self.assertIn("A bot's greeting is not an operator's greeting", messages[0]["content"])
        self.assertIn("untrusted evidence", messages[0]["content"])
        self.assertEqual(json.loads(messages[1]["content"])["conversation"]["transcript"][1]["speaker"], "operator")

    def test_unconfigured_status_is_honest(self):
        with patch("backend.cqa.evaluator.service.Settings.from_env", side_effect=EvaluatorError("Модель не подключена")):
            info = get_evaluator_info()
        self.assertFalse(info["provider_configured"])
        self.assertEqual(info["mode"], "unconfigured")

    def test_gross_error_requires_rule_and_whitespace_is_not_evidence(self):
        raw = fixture("evaluation.example.json")
        raw["results"][0]["gross_error"] = True
        with self.assertRaisesRegex(ValueError, "configured rule"):
            validate_draft(self.conversation, self.checklist, EvaluationDraft.model_validate(raw))
        raw["results"][0]["gross_error"] = False
        raw["results"][0]["evidence"][0]["quote"] = " "
        with self.assertRaisesRegex(ValueError, "exact source quote"):
            validate_draft(self.conversation, self.checklist, EvaluationDraft.model_validate(raw))


class QualityMetricsTests(unittest.TestCase):
    def setUp(self):
        conversation = normalize(DatasetExport.model_validate(fixture("synthetic_dev.json")))[0]
        checklist = ChecklistVersion.model_validate(fixture("checklist.example.json"))
        self.results = fixture("evaluation.example.json")["results"]
        self.labels = {"provenance": "synthetic_unit_test", "checklist": checklist.model_dump(mode="json"),
            "items": [{"conversation": conversation.model_dump(mode="json"), "split": "dev", "reviewed": True,
                "expected_results": deepcopy(self.results), "expected_total": 100}]}
        self.prediction = {"conversation": conversation.model_dump(mode="json"),
            "checklist": checklist.model_dump(mode="json"), "results": deepcopy(self.results), "score": {"total": 100}}

    def test_agreement_numeric_equivalence_and_unmeasured_semantics(self):
        self.prediction["results"][1]["answer"] = 2.0
        report = summarize(self.labels, [self.prediction])
        self.assertEqual(report["decisions"]["agreement"], 1)
        self.assertEqual(report["total_mae"], 0)
        self.assertEqual(report["quote_existence"]["rate"], 1)
        self.assertIsNone(report["semantic_evidence_support"]["rate"])

    def test_missing_prediction_counted_in_denominator(self):
        report = summarize(self.labels, [])
        self.assertEqual(report["decisions"]["agreement"], 0)
        self.assertEqual(report["result_coverage"], 0)
        self.assertEqual(report["missing_or_failed_predictions"], 1)
        self.assertIsNone(report["total_mae"])

    def test_unreviewed_labels_are_never_ground_truth(self):
        self.labels["items"][0]["reviewed"] = False
        report = summarize(self.labels, [self.prediction])
        self.assertEqual(report["reviewed_conversations"], 0)
        self.assertIsNone(report["decisions"]["agreement"])

    def test_changed_checklist_and_family_leakage_rejected(self):
        self.prediction["checklist"]["version"] = 2
        with self.assertRaisesRegex(ValueError, "different checklist"):
            summarize(self.labels, [self.prediction])
        item = self.labels["items"][0]
        item["source_family"] = "same-template"
        other = deepcopy(item)
        other["split"] = "holdout"
        self.labels["items"].append(other)
        with self.assertRaisesRegex(ValueError, "source_family"):
            validate_labels(self.labels, "all")


if __name__ == "__main__":
    unittest.main()
