"""Exercise edge-case UI workflows against an explicitly isolated test server.

Uses synthetic inputs and a mocked audio response. No model-quality claim.
Run scripts/browser_smoke.py first for appeals and criterion versioning.
"""
import argparse
import base64
from copy import deepcopy
import io
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit
from uuid import uuid4
import wave

import httpx
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True)
    args = parser.parse_args()
    base = args.url.rstrip('/')
    if urlsplit(base).hostname not in {'127.0.0.1', 'localhost', '::1'}:
        raise SystemExit('Only a local isolated test server is supported')
    with httpx.Client(base_url=base, timeout=15) as client:
        if client.get('/api/config').raise_for_status().json().get('mode') != 'test':
            raise SystemExit('Refusing mutations outside explicit test mode')
    out = ROOT / 'scratch'
    out.mkdir(exist_ok=True)
    dataset = json.loads((ROOT/'fixtures/readiness/chats-dataset.json').read_text(encoding='utf-8'))
    group = 'UI-READINESS-' + uuid4().hex[:8]
    dataset['data']['dataset_group_id'] = group
    dataset['data']['records'][0]['conversation_id'] = 'unseen/номер?part#1%25'
    checks, errors = [], []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel='chrome', headless=True)
        page = browser.new_page(viewport={'width':1440,'height':1000}, reduced_motion='reduce')
        page.set_default_timeout(20000)
        page.on('pageerror', lambda e: errors.append(str(e)))
        try:
            page.goto(base, wait_until='networkidle')
            expect(page.locator('.workspace-toolbar')).to_be_visible()

            def upload(value, status=200):
                with page.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith('/api/datasets/import')) as pending:
                    page.get_by_label('Выбрать JSON с диалогами').set_input_files({'name':'rehearsal.json','mimeType':'application/json','buffer':json.dumps(value,ensure_ascii=False).encode('utf-8')})
                response = pending.value
                assert response.status == status, response.status
                return response.json()

            assert upload(dataset)['imported'] == 10
            cid = dataset['data']['records'][0]['conversation_id']
            page.locator('.conversation-item').filter(has_text=cid).click()
            expect(page.get_by_role('heading',name=cid,exact=True)).to_be_visible()
            expect(page.locator('.transcript-content .message')).to_have_count(5)
            checks.append('unicode_and_reserved_character_id_opens_in_ui')
            partial = deepcopy(dataset)
            partial['data']['records'] = partial['data']['records'][:1]
            partial['data']['total_records'] = 1
            assert upload(partial)['retained_from_previous_import'] == 9
            expect(page.get_by_role('status').filter(has_text='из прошлых импортов')).to_be_visible()
            assert upload(dataset)['unchanged'] == 10
            bad = deepcopy(partial)
            bad['data']['records'][0]['transcript'] = [{'speaker':'client','timestamp':'00:00','text':'   '}]
            upload(bad,422)
            expect(page.get_by_role('alert')).to_contain_text('transcript')
            page.get_by_role('button', name='Закрыть сообщение об ошибке').click()
            checks.extend(['upsert_residual_group_warning','invalid_empty_transcript_error_visible'])

            page.get_by_role('button',name='Оценить выборку (10)',exact=True).click()
            expect(page.locator('.batch-banner')).to_contain_text('Обработка завершена',timeout=60000)
            expect(page.locator('.batch-banner')).to_contain_text('Обработано 10 из 10')
            expect(page.locator('.job-errors')).to_have_count(0)
            checks.append('ui_batch_of_ten_new_chats_completes_with_test_double')

            voices = json.loads((ROOT/'fixtures/readiness/voice-dataset.json').read_text(encoding='utf-8'))
            voices['data']['dataset_group_id'] = group + '-VOICE'
            voices['data']['records'][0]['audio_url'] = 'https://example.invalid/synthetic-player'
            upload(voices)
            first = voices['data']['records'][0]['conversation_id']
            page.locator('.conversation-item').filter(has_text=first).click()
            expect(page.locator('.audio-note')).to_contain_text('по транскрипту')
            page.route('**/api/audio-source?*', lambda route: route.fulfill(status=502,json={'detail':'Тест: источник временно недоступен.'}))
            page.get_by_role('button',name='Открыть запись звонка',exact=True).click()
            expect(page.locator('.audio-error')).to_contain_text('временно недоступен')
            page.unroute('**/api/audio-source?*')
            # Two seconds of synthetic silence prove decoding/control wiring only.
            buffer = io.BytesIO()
            with wave.open(buffer,'wb') as wav:
                wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(8000)
                wav.writeframes(b'\0\0' * 16000)
            audio = 'data:audio/wav;base64,' + base64.b64encode(buffer.getvalue()).decode()
            page.route('**/api/audio-source?*', lambda route: route.fulfill(json={'audio_url':audio,'resolved_from':'direct'}))
            page.get_by_role('button',name='Обновить ссылку на запись',exact=True).click()
            expect(page.locator('audio')).to_have_count(1)
            page.wait_for_function('document.querySelector("audio")?.readyState >= 1')
            assert page.locator('audio').evaluate('(a) => a.duration') == 2
            checks.append('audio_resolution_error_retry_and_success_decoding')
            page.screenshot(path=str(out/'readiness-ui-desktop.png'),full_page=True)
            for width,height in [(768,1024),(390,844)]:
                page.set_viewport_size({'width':width,'height':height})
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), width
            page.screenshot(path=str(out/'readiness-ui-mobile.png'),full_page=True)
            checks.append('desktop_tablet_mobile_no_horizontal_overflow')
            assert not errors, errors
        finally:
            browser.close()
    result={'status':'passed','mode':'isolated_test_double_not_AI_quality','checks':checks,'page_errors':errors,'paid_model_calls':0}
    (out/'browser-readiness-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=True))


if __name__=='__main__':
    main()
