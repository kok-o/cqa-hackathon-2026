import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from backend.cqa.audio import AudioSourceError, MAX_HTML_BYTES, resolve_audio
from backend.cqa.importer import normalize
from backend.cqa.main import create_app
from backend.cqa.models import DatasetExport, EvaluationDraft


ROOT = Path(__file__).resolve().parents[2]
WRAPPER = "https://ec88f21f1accecf38e20095a9fe175.a5.environment.api.powerplatform.com/player?token=TEST_SIGNED_VALUE"
AUDIO = "https://freedomholdingcorporation.crm11.dynamics.com/audio.mp3?token=TEST_SIGNED_VALUE&part=1"


class AudioTests(unittest.IsolatedAsyncioTestCase):
    async def resolve(self, handler, source=WRAPPER):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await resolve_audio(source, client=client)

    async def test_html_player_extracts_only_audio_sources_without_running_script(self):
        html = ('<source src="https://untrusted.example/not-audio">'
                '<script>const secret="TEST_PRIVATE_HTML"; const x="<source src=bad>";</script>'
                '<audio controls><source type="audio/mpeg" src="' + AUDIO.replace("&", "&amp;") + '"></audio>')
        result = await self.resolve(lambda _: httpx.Response(200, text=html, headers={"content-type": "text/html; charset=utf-8"}))
        self.assertEqual(result, {"audio_url": AUDIO, "resolved_from": "html"})
        self.assertNotIn("TEST_PRIVATE_HTML", json.dumps(result))
        result = await self.resolve(lambda _: httpx.Response(200, text='<audio src="' + AUDIO + '"/>',
                                                             headers={"content-type": "text/html"}))
        self.assertEqual(result["audio_url"], AUDIO)

    async def test_direct_audio_returns_without_reading_media(self):
        class UnreadAudio(httpx.AsyncByteStream):
            async def __aiter__(self):
                raise AssertionError("Resolver must not download the recording")
                yield b""
        result = await self.resolve(lambda _: httpx.Response(200, stream=UnreadAudio(), headers={"content-type": "audio/mpeg"}), AUDIO)
        self.assertEqual(result, {"audio_url": AUDIO, "resolved_from": "direct"})

    async def test_missing_untrusted_local_or_malformed_urls_never_dispatch(self):
        def unexpected(_):
            self.fail("Unsafe or missing source must not make a request")
        for source in (None, "", "http://freedomholdingcorporation.crm11.dynamics.com/a.mp3", "https://127.0.0.1/a.mp3",
                       "https://[::1]/audio", "https://untrusted.example/a?TEST_SIGNED_VALUE",
                       "https://user:TEST_SIGNED_VALUE@freedomholdingcorporation.crm11.dynamics.com/a",
                       "https://freedomholdingcorporation.crm11.dynamics.com:444/a", AUDIO + "\n"):
            with self.subTest(source=source):
                with self.assertRaises(AudioSourceError) as caught:
                    await self.resolve(unexpected, source)
                self.assertEqual(caught.exception.status_code, 404 if not source else 422)
                self.assertNotIn("TEST_SIGNED_VALUE", str(caught.exception))

    async def test_untrusted_redirect_and_embedded_url_are_rejected(self):
        calls = []
        def redirect(request):
            calls.append(request)
            return httpx.Response(302, headers={"location": "https://127.0.0.1/internal?TEST_SIGNED_VALUE"})
        with self.assertRaises(AudioSourceError):
            await self.resolve(redirect)
        self.assertEqual(len(calls), 1)
        for source in ("http://127.0.0.1/internal", "javascript:alert('TEST_SIGNED_VALUE')", "data:audio/wav;base64,TEST_SIGNED_VALUE"):
            with self.subTest(source=source):
                with self.assertRaises(AudioSourceError) as caught:
                    await self.resolve(lambda _: httpx.Response(200, text='<audio src="' + source + '"></audio>',
                                                                 headers={"content-type": "text/html"}))
                self.assertNotIn("TEST_SIGNED_VALUE", str(caught.exception))

    async def test_malformed_ambiguous_oversized_or_wrong_media_is_safe(self):
        cases = [httpx.Response(200, text="TEST_PRIVATE_HTML no audio", headers={"content-type": "text/html"}),
                 httpx.Response(200, text='<audio src="' + AUDIO + '"></audio><audio src="' + AUDIO + '2"></audio>', headers={"content-type": "text/html"}),
                 httpx.Response(200, content=b"X" * (MAX_HTML_BYTES + 1), headers={"content-type": "text/html"}),
                 httpx.Response(200, text="TEST_PRIVATE_HTML", headers={"content-type": "application/json"}),
                 httpx.Response(403, text="TEST_SIGNED_VALUE expired")]
        for response in cases:
            with self.subTest(status=response.status_code, media_type=response.headers.get("content-type")):
                with self.assertRaises(AudioSourceError) as caught:
                    await self.resolve(lambda _: response)
                self.assertNotIn("TEST_", str(caught.exception))

    async def test_allowed_redirect_and_network_errors_are_bounded_and_safe(self):
        calls = []
        def redirected(request):
            calls.append(request)
            return httpx.Response(302, headers={"location": AUDIO}) if len(calls) == 1 else httpx.Response(200, headers={"content-type": "audio/mpeg"})
        self.assertEqual((await self.resolve(redirected))["audio_url"], AUDIO)
        self.assertEqual(len(calls), 2)
        calls.clear()
        def loop(request):
            calls.append(request)
            return httpx.Response(302, headers={"location": WRAPPER})
        with self.assertRaises(AudioSourceError) as caught:
            await self.resolve(loop)
        self.assertEqual(caught.exception.status_code, 502)
        self.assertEqual(len(calls), 4)
        for exception, status in ((httpx.ReadTimeout("TEST_SIGNED_VALUE"), 504), (httpx.ConnectError("TEST_SIGNED_VALUE"), 502)):
            def fail(_):
                raise exception
            with self.assertRaises(AudioSourceError) as caught:
                await self.resolve(fail)
            self.assertEqual(caught.exception.status_code, status)
            self.assertNotIn("TEST_SIGNED_VALUE", str(caught.exception))

    async def test_total_deadline_stops_a_slow_html_stream(self):
        class SlowHtml(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b"<audio"
                await asyncio.sleep(1)
                yield b">"
        with patch("backend.cqa.audio.TOTAL_TIMEOUT_SECONDS", 0.01):
            with self.assertRaises(AudioSourceError) as caught:
                await self.resolve(lambda _: httpx.Response(200, stream=SlowHtml(), headers={"content-type": "text/html"}))
        self.assertEqual(caught.exception.status_code, 504)

    async def test_api_uses_stored_current_or_matching_evaluation_audio_snapshot(self):
        dataset = json.loads((ROOT / "fixtures/synthetic_dev.json").read_text(encoding="utf-8"))
        dataset["data"]["records"] = [dataset["data"]["records"][0]]
        dataset["data"]["total_records"] = 1
        record = dataset["data"]["records"][0]
        record.update(channel="voice call", audio_url=WRAPPER)
        with TemporaryDirectory() as temporary:
            app = create_app(Path(temporary) / "test.sqlite3", checklist_path=ROOT / "fixtures/checklist.example.json")
            async with app.router.lifespan_context(app):
                store = app.state.store
                conversation = normalize(DatasetExport.model_validate(dataset))[0]
                store.import_conversations([conversation], conversation.group_id)
                rules = store.list_checklists()[0]
                checklist = store.checklist(rules["checklist_id"], rules["version"])
                draft = EvaluationDraft.model_validate_json((ROOT / "fixtures/evaluation.example.json").read_text(encoding="utf-8"))
                job, _ = store.create_job(conversation, checklist)
                store.begin_job(job["job_id"])
                evaluation_id = store.complete_job(job["job_id"], conversation, checklist, draft, {"model": "test"})
                changed = conversation.model_copy(deep=True)
                changed.audio_url = AUDIO
                store.import_conversations([changed], changed.group_id)
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                    params = {"group_id": conversation.group_id, "conversation_id": conversation.conversation_id}
                    resolver = AsyncMock(return_value={"audio_url": AUDIO, "resolved_from": "html"})
                    with patch("backend.cqa.main.resolve_audio", resolver):
                        response = await client.get("/api/audio-source", params=params)
                        self.assertEqual(response.status_code, 200, response.text)
                        self.assertEqual(response.headers["cache-control"], "no-store")
                        resolver.assert_awaited_with(AUDIO)
                        response = await client.get("/api/audio-source", params={**params, "evaluation_id": evaluation_id})
                        self.assertEqual(response.status_code, 200)
                        resolver.assert_awaited_with(WRAPPER)
                        response = await client.get("/api/audio-source", params={**params, "conversation_id": "other", "evaluation_id": evaluation_id})
                        self.assertEqual(response.status_code, 404)
                        self.assertEqual(resolver.await_count, 2)
                    with patch("backend.cqa.main.resolve_audio", AsyncMock(side_effect=AudioSourceError("Safe failure", 502))):
                        response = await client.get("/api/audio-source", params=params)
                        self.assertEqual(response.status_code, 502)
                        self.assertEqual(response.json(), {"detail": "Safe failure"})


if __name__ == "__main__":
    unittest.main()
