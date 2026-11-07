"""No live network or provider calls: bounded media, frozen plan, billing ambiguity."""
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from scripts import transcription_pilot as pilot


SOURCE = "https://freedomholdingcorporation.crm11.dynamics.com/audio.mp3?token=PRIVATE_CANARY"


def fixture():
    records = [{"group_id": "TEST", "conversation_id": "call-1", "language": "RU", "channel": "voice call",
                "audio_url": SOURCE, "transcript": [{"message_id": "m1", "speaker": "client", "text": "Где брокерский счёт?"}]}]
    durations = {"live_calls": [{"conversation_id": "call-1", "passed": True, "playback": {"duration_seconds": 30}}]}
    anchors = {"call-1": [{"label": "account", "variants": ["брокерский счет"]}]}
    return records, durations, anchors


class TranscriptionPlanTests(unittest.TestCase):
    def test_plan_is_url_free_and_anchors_must_exist_before_call(self):
        records, durations, anchors = fixture()
        plan = pilot.make_plan(records, durations, anchors)
        self.assertNotIn("PRIVATE_CANARY", json.dumps(plan))
        self.assertNotIn(SOURCE, json.dumps(plan))
        self.assertTrue(plan["references_never_sent_to_provider"])
        pilot.validate_plan(plan, records)
        changed = deepcopy(records)
        changed[0]["audio_url"] += "changed"
        with self.assertRaises(pilot.PilotError):
            pilot.validate_plan(plan, changed)
        with self.assertRaises(pilot.PilotError):
            pilot.make_plan(records, durations, {"call-1": [{"label": "absent", "variants": ["несуществующий"]}]})

    def test_comparison_does_not_treat_noisy_reference_as_accuracy(self):
        result = pilot.comparison("Брокерский счёт открыт", "Брокерский счет уже открыт", [{"label": "account", "variants": ["брокерский счет"]}])
        self.assertEqual(result["anchor_hits"], 1)
        self.assertEqual(result["token_edit_distance"], 1)
        self.assertNotIn("accuracy", {key: value for key, value in result.items() if key != "warning"})
        self.assertIn("not_human_ground_truth", result["reference_kind"])

    def test_concurrent_run_lock_and_unknown_reservation(self):
        with TemporaryDirectory() as temp:
            with pilot.run_lock(Path(temp)):
                with self.assertRaises(pilot.PilotError):
                    with pilot.run_lock(Path(temp)):
                        pass
            self.assertFalse((Path(temp) / "run.lock").exists())
        plan = {"items": [1]}
        summary = pilot.summarize(plan, {"records": {"c": {"status": "interrupted_unknown", "reservation_usd": 0.1}}})
        self.assertEqual(summary["accounted_usd"], 0.1)
        self.assertEqual(summary["known_duration_price_estimate_usd"], 0)


class TranscriptionNetworkTests(unittest.IsolatedAsyncioTestCase):
    async def test_download_revalidates_redirect_and_never_follows_external(self):
        visited = []
        def handler(request):
            visited.append(str(request.url))
            return httpx.Response(302, headers={"location": "https://127.0.0.1/private"})
        with TemporaryDirectory() as temp, patch.object(pilot, "resolve_audio", AsyncMock(return_value={"audio_url": SOURCE})):
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                with self.assertRaises(pilot.AudioSourceError):
                    await pilot.download_audio(SOURCE, Path(temp), client=client)
        self.assertEqual(len(visited), 1)

    async def test_download_bounded_even_without_content_length(self):
        class Stream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b"x" * 6
                yield b"x" * 6
        with TemporaryDirectory() as temp, patch.object(pilot, "MAX_BYTES", 10), patch.object(pilot, "resolve_audio", AsyncMock(return_value={"audio_url": SOURCE})):
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=Stream(), headers={"content-type": "audio/mpeg"}))) as client:
                with self.assertRaises(pilot.PilotError):
                    await pilot.download_audio(SOURCE, Path(temp), client=client)
            self.assertFalse((Path(temp) / "audio.partial").exists())

    async def test_transcription_sends_only_audio_and_generic_context(self):
        calls = []
        def handler(request):
            calls.append(request)
            body = request.content.decode(errors="replace")
            self.assertIn('name="languages[]"', body)
            self.assertNotIn("брокерский счет", body)
            self.assertNotIn("PRIVATE_CANARY", body)
            return httpx.Response(200, json={"text": "Брокерский счёт открыт", "languages": [{"code": "ru"}],
                                            "usage": {"seconds": 30, "secret": "API_KEY_PRIVATE"}})
        with TemporaryDirectory() as temp:
            path = Path(temp) / "audio.mp3"
            path.write_bytes(b"FAKE AUDIO")
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                result = await pilot.transcribe(path, "audio/mpeg", "ru", "API_KEY_PRIVATE", client=client)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["usage"], {"seconds": 30})
        self.assertEqual(len(calls), 1)

    async def test_lost_response_is_reserved_and_not_replayed(self):
        records, durations, anchors = fixture()
        plan = pilot.make_plan(records, durations, anchors)
        calls = []
        def handler(request):
            calls.append(request)
            raise httpx.ReadTimeout("PRIVATE_CANARY", request=request)
        with TemporaryDirectory() as temp:
            out = Path(temp)
            audio = out / "audio.mp3"
            audio.write_bytes(b"MOCK AUDIO")
            pilot.write_json(out / "checkpoint.json", {"plan_hash": plan["plan_hash"], "records": {}})
            with patch.object(pilot, "download_audio", AsyncMock(return_value=(audio, "audio/mpeg"))), patch.object(pilot, "audio_duration", return_value=30):
                async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                    first = await pilot.run(plan, records, out, .25, "API_KEY_PRIVATE", client=client)
                    second = await pilot.run(plan, records, out, .25, "API_KEY_PRIVATE", client=client)
            self.assertEqual(first["statuses"]["call-1"], "response_lost_unknown")
            self.assertEqual(first["unknown_request_reservations_usd"], .0045)
            self.assertEqual(first, second)
            self.assertNotIn("PRIVATE_CANARY", (out / "checkpoint.json").read_text())
        self.assertEqual(len(calls), 1)

    async def test_pending_resume_and_budget_skip_make_no_dispatch(self):
        records, durations, anchors = fixture()
        plan = pilot.make_plan(records, durations, anchors)
        with TemporaryDirectory() as temp:
            out = Path(temp)
            pilot.write_json(out / "checkpoint.json", {"plan_hash": plan["plan_hash"], "records": {"call-1": {"status": "dispatching", "reservation_usd": .0045}}})
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: self.fail("unexpected network"))) as client:
                result = await pilot.run(plan, records, out, .25, "API_KEY_PRIVATE", client=client)
                self.assertEqual(result["statuses"]["call-1"], "interrupted_unknown")
                pilot.write_json(out / "checkpoint.json", {"plan_hash": plan["plan_hash"], "records": {}})
                result = await pilot.run(plan, records, out, .001, "API_KEY_PRIVATE", client=client)
                self.assertEqual(result["statuses"]["call-1"], "budget_skipped")

    async def test_fatal_access_error_stops_remaining_files_and_resume(self):
        records, durations, anchors = fixture()
        second = deepcopy(records[0])
        second["conversation_id"] = "call-2"
        records.append(second)
        durations["live_calls"].append({"conversation_id": "call-2", "passed": True, "playback": {"duration_seconds": 30}})
        anchors["call-2"] = deepcopy(anchors["call-1"])
        plan = pilot.make_plan(records, durations, anchors)
        calls = []
        def handler(request):
            calls.append(request)
            return httpx.Response(401, json={"error": "PRIVATE_CANARY"})
        with TemporaryDirectory() as temp:
            out = Path(temp)
            audio = out / "audio.mp3"
            audio.write_bytes(b"MOCK AUDIO")
            pilot.write_json(out / "checkpoint.json", {"plan_hash": plan["plan_hash"], "records": {}})
            with patch.object(pilot, "download_audio", AsyncMock(return_value=(audio, "audio/mpeg"))), patch.object(pilot, "audio_duration", return_value=30):
                async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                    first = await pilot.run(plan, records, out, .25, "API_KEY_PRIVATE", client=client)
                    second_run = await pilot.run(plan, records, out, .25, "API_KEY_PRIVATE", client=client)
            self.assertEqual(first, second_run)
            self.assertEqual(len(calls), 1)
            self.assertNotIn("call-2", second_run["statuses"])
            self.assertEqual(second_run["unknown_request_reservations_usd"], .0045)


if __name__ == "__main__":
    unittest.main()
