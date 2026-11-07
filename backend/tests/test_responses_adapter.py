"""Mocked API contract tests: no account, network, or billable calls required."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import httpx

from backend.cqa.evaluator.provider import read_usage
from backend.cqa.evaluator.service import (
    EvaluatorError, Settings, build_messages, build_request, evaluate, get_evaluator_info,
)
from backend.cqa.importer import normalize
from backend.cqa.models import ChecklistVersion, DatasetExport


FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


def fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def body(results, **changes):
    result = {"id": "resp_example", "model": "gpt-6.1-sol-2026-10-15", "status": "completed", "service_tier": "default",
        "output": [{"type": "reasoning", "summary": []},
            {"type": "message", "role": "assistant", "status": "completed", "content": [
                {"type": "output_text", "text": json.dumps({"results": results}, ensure_ascii=False)}]}],
        "usage": {"input_tokens": 100, "output_tokens": 40, "total_tokens": 140,
                  "input_tokens_details": {"cached_tokens": 20, "cache_write_tokens": 10},
                  "output_tokens_details": {"reasoning_tokens": 25}}}
    result.update(changes)
    return result


class ResponsesAdapterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.conversation = normalize(DatasetExport.model_validate(fixture("synthetic_dev.json")))[0]
        self.checklist = ChecklistVersion.model_validate(fixture("checklist.example.json"))
        self.results = fixture("evaluation.example.json")["results"]
        self.settings = Settings("https://api.openai.com/v1", "gpt-6.1-sol", "test-secret",
            api_style="responses", reasoning_effort="max", max_tokens=16384,
            input_cost_per_million=2, output_cost_per_million=10,
            cached_input_cost_per_million=0.1, cache_write_input_cost_per_million=2.5)

    async def call(self, handler, **kwargs):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await evaluate(self.conversation, self.checklist,
                settings=kwargs.pop("settings", self.settings), client=client, **kwargs)

    async def test_strict_request_max_effort_without_unsupported_sampling_or_storage(self):
        original = self.conversation.model_dump(mode="json")
        def handler(request):
            self.assertEqual(str(request.url), "https://api.openai.com/v1/responses")
            payload = json.loads(request.content)
            self.assertEqual(payload["reasoning"], {"effort": "max"})
            self.assertEqual(payload["max_output_tokens"], 16384)
            self.assertFalse(payload["store"])
            self.assertEqual(payload["tools"], [])
            self.assertEqual(payload["tool_choice"], "none")
            self.assertEqual(payload["service_tier"], "default")
            self.assertNotIn("temperature", payload)
            self.assertNotIn("max_tokens", payload)
            self.assertNotIn("messages", payload)
            schema = payload["text"]["format"]
            self.assertEqual(schema["type"], "json_schema")
            self.assertTrue(schema["strict"])
            self.assertFalse(schema["schema"]["additionalProperties"])
            self.assertNotIn("audio_url", json.loads(payload["input"][1]["content"])["conversation"])
            return httpx.Response(200, json=body(self.results))
        run = await self.call(handler)
        self.assertEqual(self.conversation.model_dump(mode="json"), original)
        self.assertEqual(run.model_info["reasoning_tokens"], 25)
        self.assertEqual(run.model_info["cached_input_tokens"], 20)
        self.assertEqual(run.model_info["cache_write_input_tokens"], 10)
        self.assertEqual(run.model_info["response_models"], ["gpt-6.1-sol-2026-10-15"])
        self.assertEqual(run.model_info["attempt_details"][0]["response_id"], "resp_example")
        self.assertEqual(run.model_info["attempt_details"][0]["response_service_tier"], "default")
        self.assertEqual(run.model_info["response_service_tiers"], ["default"])
        self.assertTrue(run.model_info["service_tier_verified"])
        self.assertNotIn("temperature", run.model_info)
        self.assertAlmostEqual(run.model_info["estimated_cost"], 0.000567)

    async def test_repair_revalidates_evidence_and_counts_both_attempts(self):
        invalid = deepcopy(self.results)
        invalid[0]["evidence"][0]["quote"] = "made up quote"
        requests = []
        def handler(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, json=body(invalid if len(requests) == 1 else self.results))
        run = await self.call(handler)
        self.assertEqual(run.model_info["attempts"], 2)
        self.assertEqual(run.model_info["input_tokens"], 200)
        self.assertEqual(run.model_info["output_tokens"], 80)
        self.assertEqual(run.model_info["reasoning_tokens"], 50)
        self.assertAlmostEqual(run.model_info["estimated_cost"], 0.001134)
        self.assertEqual(requests[1]["reasoning"]["effort"], "max")
        self.assertEqual(len(requests[1]["input"]), 3)
        self.assertNotIn("made up quote", json.dumps(requests[1]))
        self.assertEqual(run.model_info["attempt_details"][0]["status"], "invalid_response")

    async def test_refusal_not_retried_and_failed_usage_preserved(self):
        calls = []
        def handler(request):
            calls.append(request)
            rejected = body([], output=[{"type": "message", "role": "assistant", "content": [
                {"type": "refusal", "refusal": "test-secret sensitive rejection"}]}])
            return httpx.Response(200, json=rejected)
        with self.assertRaises(EvaluatorError) as caught:
            await self.call(handler)
        error = caught.exception
        self.assertEqual(error.code, "refusal")
        self.assertEqual(len(calls), 1)
        self.assertEqual(error.model_info["total_tokens"], 140)
        self.assertAlmostEqual(error.model_info["estimated_cost"], 0.000567)
        self.assertNotIn("test-secret", str(error) + json.dumps(error.model_info))

    async def test_incomplete_retries_without_lowering_effort_and_accounts_tokens(self):
        requests = []
        def handler(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, json=body([], status="incomplete", output=[],
                incomplete_details={"reason": "max_output_tokens"}))
        with self.assertRaises(EvaluatorError) as caught:
            await self.call(handler)
        self.assertEqual(caught.exception.code, "incomplete")
        self.assertEqual(caught.exception.model_info["attempts"], 2)
        self.assertEqual(caught.exception.model_info["output_tokens"], 80)
        self.assertTrue(all(request["reasoning"]["effort"] == "max" for request in requests))
        self.assertTrue(all(request["max_output_tokens"] == 16384 for request in requests))

    async def test_empty_output_and_invalid_json_are_bounded_failures(self):
        for output in ([], [{"type": "message", "role": "assistant", "content": [
                {"type": "output_text", "text": "{"}]}]):
            with self.subTest(output=output):
                with self.assertRaises(EvaluatorError) as caught:
                    await self.call(lambda _: httpx.Response(200, json=body([], output=output)))
                self.assertEqual(caught.exception.code, "invalid_response")
                self.assertEqual(caught.exception.model_info["attempts"], 2)
                self.assertEqual(caught.exception.model_info["input_tokens"], 200)

    async def test_content_filter_and_unexpected_tools_never_retry(self):
        responses = [body([], status="incomplete", output=[], incomplete_details={"reason": "content_filter"}),
                     body([], output=[{"type": "function_call", "name": "anything", "arguments": "{}"}])]
        for response, code in zip(responses, ("content_filter", "unexpected_tool_call")):
            with self.subTest(code=code):
                with self.assertRaises(EvaluatorError) as caught:
                    await self.call(lambda _: httpx.Response(200, json=response))
                self.assertEqual(caught.exception.code, code)
                self.assertEqual(caught.exception.model_info["attempts"], 1)

    async def test_filtered_partial_message_is_not_retried_as_token_truncation(self):
        calls = []
        filtered = body([], status="incomplete", incomplete_details={"reason": "content_filter"},
            output=[{"type": "reasoning", "summary": []},
                    {"type": "message", "role": "assistant", "status": "incomplete", "content": [
                        {"type": "output_text", "text": '{"results":['}]}])
        def handler(request):
            calls.append(request)
            return httpx.Response(200, json=filtered)
        with self.assertRaises(EvaluatorError) as caught:
            await self.call(handler)
        self.assertEqual(caught.exception.code, "content_filter")
        self.assertEqual(len(calls), 1)
        self.assertEqual(caught.exception.model_info["attempts"], 1)
        self.assertEqual(caught.exception.model_info["output_tokens"], 40)
        self.assertAlmostEqual(caught.exception.model_info["estimated_cost"], 0.000567)

    async def test_nonstandard_or_missing_openai_tier_keeps_usage_but_not_standard_cost(self):
        for tier in ("fast", "priority", "flex", "scale", "ultrafast", "auto", None):
            with self.subTest(tier=tier):
                response = body(self.results, service_tier=tier)
                if tier is None:
                    response.pop("service_tier")
                run = await self.call(lambda _: httpx.Response(200, json=response))
                self.assertTrue(run.model_info["usage_complete"])
                self.assertEqual(run.model_info["total_tokens"], 140)
                self.assertIsNone(run.model_info["estimated_cost"])
                self.assertFalse(run.model_info["service_tier_verified"])
                self.assertEqual(run.model_info["requested_service_tier"], "default")
                self.assertEqual(run.model_info["attempt_details"][0]["response_service_tier"], tier)

    async def test_openai_chat_pins_default_tier_and_rejects_premium_price_assumption(self):
        settings = replace(self.settings, api_style="chat_completions")
        for tier in ("default", "priority"):
            with self.subTest(tier=tier):
                def handler(request):
                    self.assertEqual(str(request.url), "https://api.openai.com/v1/chat/completions")
                    self.assertEqual(json.loads(request.content)["service_tier"], "default")
                    return httpx.Response(200, json={"model": settings.model, "service_tier": tier,
                        "choices": [{"message": {"content": json.dumps({"results": self.results})}, "finish_reason": "stop"}],
                        "usage": {"prompt_tokens": 100, "completion_tokens": 40}})
                run = await self.call(handler, settings=settings)
                self.assertEqual(run.model_info["response_service_tiers"], [tier])
                if tier == "default":
                    self.assertAlmostEqual(run.model_info["estimated_cost"], 0.0006)
                else:
                    self.assertIsNone(run.model_info["estimated_cost"])

    def test_service_tier_parameter_is_not_sent_to_local_or_compatible_providers(self):
        for api_style in ("responses", "chat_completions"):
            for base_url in ("http://localhost:11434/v1", "https://compatible.example/v1"):
                with self.subTest(api_style=api_style, base_url=base_url):
                    request = build_request(replace(self.settings, api_style=api_style, base_url=base_url), [])
                    self.assertNotIn("service_tier", request)

    async def test_failed_response_and_http_retry_costs_are_not_dropped(self):
        for first in (httpx.Response(200, json=body([], status="failed", output=[], error={"message": "secret"})),
                      httpx.Response(500, json=body([], error={"message": "secret"}))):
            with self.subTest(status=first.status_code):
                calls = []
                def handler(request):
                    calls.append(request)
                    return first if len(calls) == 1 else httpx.Response(200, json=body(self.results))
                run = await self.call(handler)
                self.assertEqual(run.model_info["input_tokens"], 200)
                self.assertAlmostEqual(run.model_info["estimated_cost"], 0.001134)

    async def test_auth_and_unsupported_parameter_errors_are_safe_and_fatal(self):
        for status, code in ((401, "auth"), (403, "auth"), (400, "request"), (404, "request")):
            with self.subTest(status=status):
                with self.assertRaises(EvaluatorError) as caught:
                    await self.call(lambda _: httpx.Response(status, json={"error": {"message": "test-secret"}}))
                self.assertEqual(caught.exception.code, code)
                self.assertEqual(caught.exception.model_info["attempts"], 1)
                self.assertFalse(caught.exception.model_info["usage_complete"])
                self.assertIsNone(caught.exception.model_info["estimated_cost"])
                self.assertNotIn("test-secret", str(caught.exception) + json.dumps(caught.exception.model_info))

    async def test_timeout_then_success_retains_unknown_cost_and_known_attempt(self):
        calls = []
        def handler(request):
            calls.append(request)
            if len(calls) == 1:
                raise httpx.ReadTimeout("test-secret", request=request)
            return httpx.Response(200, json=body(self.results))
        run = await self.call(handler)
        self.assertEqual(run.model_info["attempts"], 2)
        self.assertFalse(run.model_info["usage_complete"])
        self.assertIsNone(run.model_info["estimated_cost"])
        self.assertAlmostEqual(run.model_info["known_estimated_cost"], 0.000567)

    async def test_missing_cache_breakdown_or_price_never_fabricates_cost(self):
        variants = [body(self.results, usage={"input_tokens": 100, "output_tokens": 40}), body(self.results)]
        for response in variants:
            with self.subTest(response=response):
                run = await self.call(lambda _: httpx.Response(200, json=response),
                    settings=replace(self.settings, cached_input_cost_per_million=None))
                self.assertEqual(run.model_info["input_tokens"], 100)
                self.assertIsNone(run.model_info["estimated_cost"])

    async def test_long_context_multiplier_is_per_attempt(self):
        response = body(self.results)
        response["usage"].update(input_tokens=300000, total_tokens=300040)
        run = await self.call(lambda _: httpx.Response(200, json=response))
        expected = ((300000 - 30) * 4 + 20 * 0.2 + 10 * 5 + 40 * 15) / 1_000_000
        self.assertAlmostEqual(run.model_info["estimated_cost"], expected)

    def test_local_defaults_and_explicit_endpoint_switch_are_compatible(self):
        local = Settings("http://localhost:11434/v1/chat/completions", "local-model")
        payload = build_request(local, build_messages(self.conversation, self.checklist))
        self.assertEqual(local.api_style, "chat_completions")
        self.assertEqual(payload["temperature"], 0)
        self.assertEqual(payload["max_tokens"], 6000)
        self.assertEqual(replace(local, api_style="responses").endpoint, "http://localhost:11434/v1/responses")
        self.assertEqual(replace(self.settings, base_url="https://api.openai.com/v1/responses/").endpoint,
                         "https://api.openai.com/v1/responses")
        info = get_evaluator_info(self.settings)
        self.assertEqual(info["reasoning_effort"], "max")
        self.assertEqual(info["json_mode"], "json_schema")

    def test_settings_read_explicit_api_effort_and_prices_without_silent_downgrade(self):
        env = {"LLM_BASE_URL": "https://api.openai.com/v1", "LLM_MODEL": "gpt-6-luna",
               "LLM_API_STYLE": "responses", "LLM_REASONING_EFFORT": "max",
               "LLM_CACHED_INPUT_COST_PER_MILLION": "0.01", "LLM_CACHE_WRITE_INPUT_COST_PER_MILLION": "0.125"}
        with patch("backend.cqa.evaluator.service.load_dotenv"), patch.dict("os.environ", env, clear=True):
            settings = Settings.from_env()
        self.assertEqual(settings.reasoning_effort, "max")
        self.assertEqual(settings.cache_write_input_cost_per_million, 0.125)
        with self.assertRaises(EvaluatorError):
            replace(self.settings, reasoning_effort="ultra").validate()
        with self.assertRaises(EvaluatorError):
            replace(self.settings, reasoning_effort="none").validate()
        replace(self.settings, max_tokens=128000).validate()

    def test_invalid_usage_retains_reported_counts_without_claiming_complete(self):
        usage = read_usage({"usage": {"input_tokens": 100, "output_tokens": "40"}}, "responses")
        self.assertEqual(usage.input_tokens, 100)
        self.assertFalse(usage.complete)
        for invalid in (True, -1, 101):
            response = body([])
            response["usage"]["input_tokens_details"]["cached_tokens"] = invalid
            self.assertFalse(read_usage(response, "responses").complete)


if __name__ == "__main__":
    unittest.main()
