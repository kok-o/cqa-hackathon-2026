"""Isolated real HTTP fault checks; no external provider or credentials."""

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time
import unittest

import httpx

from backend.cqa.evaluator.service import EvaluatorError, Settings, evaluate
from backend.cqa.importer import normalize
from backend.cqa.models import ChecklistVersion, DatasetExport
from backend.tests.test_responses_adapter import body, fixture


class ProviderFaultTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.conversation = normalize(DatasetExport.model_validate(fixture("synthetic_dev.json")))[0]
        self.checklist = ChecklistVersion.model_validate(fixture("checklist.example.json"))
        self.results = fixture("evaluation.example.json")["results"]
        self.mode = "valid"
        self.retry_header = "1"
        self.requests = []
        self.stop = threading.Event()
        test = self
        payload = json.dumps(body(self.results)).encode("utf-8")

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers["content-length"]))
                requested_at = time.monotonic()
                test.requests.append(requested_at)
                failed = test.mode == "cooldown" and requested_at - test.requests[0] < 1
                if test.mode == "long_cooldown" or failed:
                    content = b'{"error":{"message":"TEST_PRIVATE_PROVIDER_BODY"}}'
                    self.send_response(429)
                    self.send_header("Retry-After", test.retry_header)
                else:
                    content = payload
                    self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                try:
                    if test.mode == "trickle":
                        chunk_size = (len(content) + 14) // 15
                        for offset in range(0, len(content), chunk_size):
                            self.wfile.write(content[offset:offset + chunk_size])
                            self.wfile.flush()
                            if test.stop.wait(0.15):
                                return
                    else:
                        self.wfile.write(content)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass  # A timeout deliberately closes the reader mid-response.

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=0.01), daemon=True)
        self.thread.start()
        self.settings = Settings(f"http://127.0.0.1:{self.server.server_port}/v1", "test-provider",
                                 api_key="TEST_SECRET_PROVIDER_CANARY", timeout_seconds=1,
                                 api_style="responses", input_cost_per_million=2, output_cost_per_million=10,
                                 cached_input_cost_per_million=0.1, cache_write_input_cost_per_million=2.5)

    async def asyncTearDown(self):
        self.stop.set()
        await asyncio.to_thread(self.server.shutdown)
        self.server.server_close()
        self.thread.join(timeout=1)

    async def test_real_http_trickle_obeys_wall_deadline_and_two_attempt_limit(self):
        self.mode = "trickle"
        started = time.monotonic()
        with self.assertRaises(EvaluatorError) as caught:
            await evaluate(self.conversation, self.checklist, settings=self.settings)
        elapsed = time.monotonic() - started
        error = caught.exception
        self.assertEqual(error.code, "timeout")
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(error.model_info["attempts"], 2)
        self.assertTrue(all(attempt["status"] == "timeout" for attempt in error.model_info["attempt_details"]))
        self.assertLess(elapsed, 4)
        self.assertFalse(error.model_info["usage_complete"])
        self.assertIsNone(error.model_info["estimated_cost"])
        self.assertEqual(error.model_info["known_estimated_cost"], 0)
        self.assertNotIn(self.settings.api_key, str(error) + json.dumps(error.model_info))

    async def test_real_http_retry_after_waits_for_cooldown_and_preserves_unknown_usage(self):
        self.mode = "cooldown"
        run = await evaluate(self.conversation, self.checklist, settings=self.settings)
        self.assertEqual(len(self.requests), 2)
        self.assertGreaterEqual(self.requests[1] - self.requests[0], 1)
        self.assertEqual(run.model_info["attempts"], 2)
        self.assertEqual(run.model_info["attempt_details"][0]["retry_after_seconds"], 1)
        self.assertFalse(run.model_info["usage_complete"])
        self.assertIsNone(run.model_info["estimated_cost"])
        self.assertAlmostEqual(run.model_info["known_estimated_cost"], 0.000567)
        self.assertNotIn("TEST_PRIVATE_PROVIDER_BODY", json.dumps(run.model_info))

    async def test_real_http_long_seconds_or_date_cooldown_never_retries_early(self):
        self.mode = "long_cooldown"
        for header, timeout in (("2", 1), ("31", 600),
                                (format_datetime(datetime.now(timezone.utc) + timedelta(seconds=120), usegmt=True), 600)):
            with self.subTest(header=header, timeout=timeout):
                self.requests.clear()
                self.retry_header = header
                with self.assertRaises(EvaluatorError) as caught:
                    await evaluate(self.conversation, self.checklist, settings=replace(self.settings, timeout_seconds=timeout))
                self.assertEqual(caught.exception.code, "http")
                self.assertEqual(len(self.requests), 1)
                self.assertEqual(caught.exception.model_info["attempts"], 1)
                detail = caught.exception.model_info["attempt_details"][0]
                self.assertGreater(detail["retry_after_seconds"], min(timeout, 30))
                self.assertEqual(detail["retry_skipped"], "retry_after_exceeds_limit")

    async def test_invalid_retry_after_does_not_echo_header_and_past_date_is_zero(self):
        for header in ("TEST_PRIVATE_INVALID_HEADER", format_datetime(datetime(2000, 1, 1, tzinfo=timezone.utc), usegmt=True)):
            calls = []
            def handler(request):
                calls.append(request)
                return (httpx.Response(503, headers={"Retry-After": header}) if len(calls) == 1
                        else httpx.Response(200, json=body(self.results)))
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                run = await evaluate(self.conversation, self.checklist, settings=self.settings, client=client)
            self.assertEqual(run.model_info["attempts"], 2)
            self.assertNotIn("TEST_PRIVATE_INVALID_HEADER", json.dumps(run.model_info))
            detail = run.model_info["attempt_details"][0]
            if header.startswith("Sat,"):
                self.assertEqual(detail["retry_after_seconds"], 0)
            else:
                self.assertNotIn("retry_after_seconds", detail)

    async def test_echoed_key_in_provider_model_and_response_id_is_never_saved(self):
        for model, response_id in ((self.settings.api_key, "resp_" + self.settings.api_key),
                                   ("prefix_" + self.settings.api_key + "_suffix", self.settings.api_key)):
            response = body(self.results, model=model, id=response_id)
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response))) as client:
                run = await evaluate(self.conversation, self.checklist, settings=self.settings, client=client)
            self.assertNotIn(self.settings.api_key, json.dumps(run.model_info))
            self.assertEqual(run.model_info["response_models"], [])
            self.assertIsNone(run.model_info["attempt_details"][0]["response_model"])
            self.assertIsNone(run.model_info["attempt_details"][0]["response_id"])
            self.assertAlmostEqual(run.model_info["estimated_cost"], 0.000567)


if __name__ == "__main__":
    unittest.main()
