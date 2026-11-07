"""FreedomSpeech preparation: deterministic transport doubles, never real API calls."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from scripts import freedomspeech_pilot as pilot


def fixture(count=1, tariff=True):
    records, live, anchors = [], [], {}
    for index in range(count):
        cid = f"call-{index}"
        records.append({"group_id": "TEST", "conversation_id": cid, "language": "RU", "channel": "voice call",
                        "audio_url": "https://freedomholdingcorporation.crm11.dynamics.com/audio.mp3?token=PRIVATE_SOURCE",
                        "transcript": [{"message_id": "m1", "speaker": "client", "text": "Брокерский счет"}]})
        live.append({"conversation_id": cid, "passed": True, "playback": {"duration_seconds": 30}})
        anchors[cid] = [{"label": "account", "variants": ["брокерский счет"]}]
    rates = {"rate_per_minute_usd": .1, "quota_minutes": 10, "billing_increment_seconds": 60,
             "source": "Test-only simulated tariff, not vendor pricing", "verified_at": "2026-11-07", "user_verified": True} if tariff else None
    return records, pilot.make_plan(records, {"live_calls": live}, anchors, rates)


class FreedomSpeechPlanTests(unittest.TestCase):
    def test_dry_plan_has_no_invented_price_or_source_url(self):
        records, plan = fixture(tariff=False)
        pilot.validate_plan(plan, records)
        self.assertIsNone(plan["tariff"])
        self.assertIsNone(plan["items"][0]["reservation_usd"])
        self.assertNotIn("PRIVATE_SOURCE", json.dumps(plan))
        self.assertEqual(plan["request_fields"], {"prettify": "false", "response_format": "diarized_json"})
        self.assertFalse(plan["known_speaker_names_sent"])

    def test_tariff_requires_explicit_verified_rate_quota_increment(self):
        with TemporaryDirectory() as temp:
            path = Path(temp) / "tariff.json"
            pilot.shared.write_json(path, {"rate_per_minute_usd": 0})
            with self.assertRaises(pilot.shared.PilotError):
                pilot.read_tariff(path)
            _, plan = fixture()
            pilot.shared.write_json(path, plan["tariff"])
            self.assertEqual(pilot.read_tariff(path), plan["tariff"])

    def test_diarization_preserves_labels_without_assigning_roles(self):
        body = {"text": "A: Счет. B: Спасибо", "language": "ru", "segments": [
            {"speaker": "A", "start": 0, "end": 1.5, "text": "Счет."},
            {"speaker": "B", "start": 1.4, "end": 3, "text": "Спасибо"}]}
        result = pilot.parse_response(body, 4, "PRIVATE_KEY")
        self.assertEqual(result["status"], "completed")
        self.assertEqual([s["speaker"] for s in result["segments"]], ["A", "B"])
        self.assertIsNone(result["speaker_role_mapping"])
        self.assertEqual(result["comparison_text"], "Счет.\nСпасибо")
        self.assertFalse(result["diarization_correctness_verified"])
        body["segments"][1]["end"] = 100
        self.assertEqual(pilot.parse_response(body, 4, "PRIVATE_KEY")["status"], "invalid_response")

    def test_missing_segments_or_secret_echo_are_not_saved_as_success(self):
        self.assertEqual(pilot.parse_response({"text": "A text"}, 10, "KEY")["status"], "invalid_response")
        self.assertEqual(pilot.parse_response({"text": "KEY", "segments": []}, 10, "KEY")["status"], "invalid_response")

    def test_cached_audio_requires_matching_source_plan_and_file_hash(self):
        records, plan = fixture()
        with TemporaryDirectory() as temp, patch.object(pilot, "ROOT", Path(temp)):
            directory = Path(temp) / "data/evaluations/cache"
            directory.mkdir(parents=True)
            audio = directory / "audio.mp3"
            audio.write_bytes(b"AUDIO")
            pilot.shared.write_json(directory / "plan.json", plan)
            pilot.shared.write_json(directory / "checkpoint.json", {"plan_hash": plan["plan_hash"], "records": {"call-0": {
                "audio_path": "audio.mp3", "audio_sha256": hashlib.sha256(audio.read_bytes()).hexdigest()}}})
            result = pilot.reusable_audio(directory, "call-0", plan["items"][0])
            self.assertEqual(result[0], audio)
            audio.write_bytes(b"CHANGED")
            with self.assertRaises(pilot.shared.PilotError):
                pilot.reusable_audio(directory, "call-0", plan["items"][0])


class FreedomSpeechTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_multipart_uses_only_freedom_key_and_raw_diarized_fields(self):
        calls = []
        def handler(request):
            calls.append(request)
            self.assertEqual(str(request.url), pilot.ENDPOINT)
            self.assertEqual(request.headers["X-API-Key"], "PRIVATE_KEY")
            self.assertNotIn("Authorization", request.headers)
            content = request.content.decode(errors="replace")
            self.assertIn('name="prettify"\r\n\r\nfalse', content)
            self.assertIn('name="response_format"\r\n\r\ndiarized_json', content)
            self.assertNotIn("known_speaker_names", content)
            self.assertNotIn('name="model"', content)
            return httpx.Response(200, json={"text": "A: Счет", "language": "ru", "segments": [{"start": 0, "end": 1, "text": "Счет", "speaker": "A"}]})
        with TemporaryDirectory() as temp:
            audio = Path(temp) / "audio.mp3"
            audio.write_bytes(b"MOCK_AUDIO")
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                result = await pilot.transcribe(audio, "audio/mpeg", 30, "PRIVATE_KEY", client=client)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(calls), 1)

    async def test_missing_key_or_tariff_never_downloads_or_dispatches(self):
        records, plan = fixture(tariff=False)
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: self.fail("unexpected network"))) as client:
            with self.assertRaises(pilot.shared.PilotError):
                await pilot.run(plan, records, Path("unused"), 1, "", client=client)
            with self.assertRaises(pilot.shared.PilotError):
                await pilot.run(plan, records, Path("unused"), 1, "KEY", client=client)

    async def test_auth_failure_stops_all_files_and_persists_on_resume(self):
        records, plan = fixture(count=2)
        calls = []
        def handler(request):
            calls.append(request)
            return httpx.Response(401, json={"error": "PRIVATE_KEY not allowed"})
        with TemporaryDirectory() as temp:
            out = Path(temp)
            audio = out / "audio.mp3"
            audio.write_bytes(b"MOCK_AUDIO")
            pilot.shared.write_json(out / "checkpoint.json", {"plan_hash": plan["plan_hash"], "records": {}})
            with patch.object(pilot.shared, "download_audio", AsyncMock(return_value=(audio, "audio/mpeg"))), patch.object(pilot.shared, "audio_duration", return_value=30):
                async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                    first = await pilot.run(plan, records, out, 1, "PRIVATE_KEY", client=client)
                    second = await pilot.run(plan, records, out, 1, "PRIVATE_KEY", client=client)
            self.assertEqual(first, second)
            self.assertEqual(first["accounted_usd"], .1)
            self.assertEqual(list(first["statuses"]), ["call-0"])
            self.assertNotIn("PRIVATE_KEY", (out / "checkpoint.json").read_text())
        self.assertEqual(len(calls), 1)

    async def test_interrupted_call_reserves_quota_and_is_never_replayed(self):
        records, plan = fixture()
        with TemporaryDirectory() as temp:
            out = Path(temp)
            pilot.shared.write_json(out / "checkpoint.json", {"plan_hash": plan["plan_hash"], "records": {
                "call-0": {"status": "dispatching", "reservation_usd": .1, "reserved_minutes": 1}}})
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: self.fail("unexpected network"))) as client:
                result = await pilot.run(plan, records, out, 1, "KEY", client=client)
            self.assertEqual(result["statuses"]["call-0"], "interrupted_unknown")
            self.assertEqual(result["accounted_usd"], .1)
            self.assertEqual(result["accounted_quota_minutes"], 1)

    async def test_budget_prevents_download_and_dispatch(self):
        records, plan = fixture()
        with TemporaryDirectory() as temp:
            out = Path(temp)
            pilot.shared.write_json(out / "checkpoint.json", {"plan_hash": plan["plan_hash"], "records": {}})
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: self.fail("unexpected network"))) as client:
                result = await pilot.run(plan, records, out, .01, "KEY", client=client)
            self.assertEqual(result["statuses"]["call-0"], "budget_or_quota_skipped")
            self.assertEqual(result["accounted_usd"], 0)

    async def test_quota_prevents_dispatch_even_with_sufficient_money(self):
        records, plan = fixture()
        plan["tariff"]["quota_minutes"] = .1
        plan["plan_hash"] = pilot.shared.digest({k: v for k, v in plan.items() if k not in {"plan_hash", "created_at"}})
        with TemporaryDirectory() as temp:
            out = Path(temp)
            pilot.shared.write_json(out / "checkpoint.json", {"plan_hash": plan["plan_hash"], "records": {}})
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: self.fail("unexpected network"))) as client:
                result = await pilot.run(plan, records, out, 1, "KEY", client=client)
            self.assertEqual(result["statuses"]["call-0"], "budget_or_quota_skipped")


if __name__ == "__main__":
    unittest.main()
