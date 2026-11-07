"""Exercise edge-case UI workflows against an explicitly isolated test server.

Uses synthetic inputs and a mocked audio response. No model-quality claim.
Run scripts/browser_smoke.py first for appeals and criterion versioning.
"""
import argparse
import base64
from copy import deepcopy
import io
import json
import re
from pathlib import Path
import sys
from urllib.parse import urlsplit
from uuid import uuid4
import wave

import httpx
from playwright.sync_api import Error as PlaywrightError, sync_playwright, expect
from select_control import choose_select, select_control

ROOT = Path(__file__).resolve().parents[1]


def check_audio_source_refresh(page, voices, upload, audio, out):
    """Reimport the selected source while playback is loaded or resolution is pending."""
    evidence = {'mode': 'synthetic_audio_only', 'paid_model_calls': 0, 'checks': {}}
    changed = deepcopy(voices)
    changed['data']['records'][0]['audio_url'] = 'https://example.invalid/source-version-two'
    changed['data']['records'][0]['transcript'][0]['text'] = 'Synthetic audio source version two'
    upload(changed)
    expect(page.locator('.transcript-content')).to_contain_text('Synthetic audio source version two')
    # Source and transcript have refreshed; the earlier media element must go.
    evidence['checks']['loaded_audio_cleared_on_source_change'] = page.locator('audio').count() == 0
    evidence['checks']['new_source_can_be_opened'] = page.get_by_role('button', name='Открыть запись звонка', exact=True).is_visible()

    page.unroute('**/api/audio-source?*')
    page.reload(wait_until='networkidle')
    first = voices['data']['records'][0]['conversation_id']
    page.locator('.conversation-item').filter(has_text=first).click()
    pending = []
    page.route('**/api/audio-source?*', lambda route: pending.append(route))
    with page.expect_request('**/api/audio-source?*'):
        page.get_by_role('button', name='Открыть запись звонка', exact=True).click()
    expect(page.get_by_role('button', name='Подключаем запись…', exact=True)).to_be_visible()
    assert len(pending) == 1
    changed['data']['records'][0]['audio_url'] = 'https://example.invalid/source-version-three'
    changed['data']['records'][0]['transcript'][0]['text'] = 'Synthetic audio source version three'
    upload(changed)
    expect(page.locator('.transcript-content')).to_contain_text('Synthetic audio source version three')
    evidence['checks']['pending_source_change_resets_loading'] = page.get_by_role('button', name='Открыть запись звонка', exact=True).is_visible()
    # Release the obsolete resolver response after the replacement source renders.
    # An aborted request may already have been discarded by the browser.
    try:
        pending[0].fulfill(json={'audio_url': audio, 'resolved_from': 'direct'})
    except PlaywrightError:
        if not evidence['checks']['pending_source_change_resets_loading']:
            raise
    page.wait_for_load_state('networkidle')
    evidence['checks']['obsolete_resolution_does_not_attach_audio'] = page.locator('audio').count() == 0
    page.unroute('**/api/audio-source?*')
    page.route('**/api/audio-source?*', lambda route: route.fulfill(json={'audio_url': audio, 'resolved_from': 'direct'}))
    passed = all(evidence['checks'].values())
    evidence['status'] = 'passed' if passed else 'failed'
    (out / 'audio-source-refresh-result.json').write_text(json.dumps(evidence, indent=2), encoding='utf-8')
    assert passed, 'Audio source refresh regressions: ' + ', '.join(name for name, ok in evidence['checks'].items() if not ok)
    page.get_by_role('button', name='Открыть запись звонка', exact=True).click()
    expect(page.locator('audio')).to_have_count(1)
    page.wait_for_function('document.querySelector("audio")?.readyState >= 1')
    assert page.locator('audio').evaluate('(element) => element.duration') == 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True)
    args = parser.parse_args()
    base = args.url.rstrip('/')
    if urlsplit(base).hostname not in {'127.0.0.1', 'localhost', '::1'}:
        raise SystemExit('Only a local isolated test server is supported')
    with httpx.Client(base_url=base, timeout=15) as client:
        config = client.get('/api/config').raise_for_status().json()
        if config.get('mode') != 'test':
            raise SystemExit('Refusing mutations outside explicit test mode')
    out = ROOT / 'scratch'
    out.mkdir(exist_ok=True)
    dataset = json.loads((ROOT/'fixtures/readiness/chats-dataset.json').read_text(encoding='utf-8'))
    group = 'UI-READINESS-' + uuid4().hex[:8]
    dataset['data']['dataset_group_id'] = group
    dataset['data']['records'][0]['conversation_id'] = 'unseen/номер?part#1%25-' + group[-8:]
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
            expect(page.locator('#conversation-title')).to_have_text(cid)
            expect(page.locator('.transcript-content .message')).to_have_count(5)
            checks.append('unicode_and_reserved_character_id_opens_in_ui')
            partial = deepcopy(dataset)
            partial['data']['records'] = partial['data']['records'][:1]
            partial['data']['total_records'] = 1
            assert upload(partial)['retained_from_previous_import'] == 9
            expect(page.get_by_role('status').filter(has_text='из предыдущих импортов')).to_be_visible()
            assert upload(dataset)['unchanged'] == 10
            bad = deepcopy(partial)
            bad['data']['records'][0]['transcript'] = [{'speaker':'client','timestamp':'00:00','text':'   '}]
            upload(bad,422)
            expect(page.get_by_role('alert')).to_contain_text('transcript')
            page.get_by_role('button', name='Закрыть сообщение об ошибке').click()
            checks.extend(['upsert_residual_group_warning','invalid_empty_transcript_error_visible'])

            choose_select(select_control(page, 'Версия чек-листа'), json.dumps(['synthetic-checklist', 1], separators=(',', ':')))
            page.get_by_role('button',name=re.compile(r'^Оценить выборку\s*10$')).click()
            expect(page.locator('.batch-banner')).to_contain_text('Обработка завершена',timeout=60000)
            expect(page.locator('.batch-banner')).to_contain_text('Обработано 10 из 10')
            expect(page.locator('.job-errors')).to_have_count(0)
            checks.append('ui_batch_of_ten_new_chats_completes_with_test_double')

            official = json.loads((ROOT/'fixtures/checklist.official.json').read_text(encoding='utf-8'))
            with httpx.Client(base_url=base, timeout=15) as client:
                existing = client.get('/api/checklists').raise_for_status().json()
                if not any(item['checklist_id'] == official['checklist_id'] for item in existing):
                    client.post('/api/checklists', json=official).raise_for_status()
            page.reload(wait_until='networkidle')
            page.get_by_role('searchbox', name='Поиск диалога').fill(cid)
            page.locator('.conversation-item').filter(has_text=cid).click()
            official_key = json.dumps([official['checklist_id'], official['version']], separators=(',', ':'))
            choose_select(select_control(page, 'Версия чек-листа'), official_key)
            with page.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith('/api/evaluation-jobs')) as pending:
                page.get_by_role('button', name='Оценить снова', exact=True).click()
            assert pending.value.status == 202
            expect(page.locator('.batch-banner')).to_contain_text('Обработка завершена')
            page.get_by_role('button', name='Файл для сдачи', exact=True).click()
            dialog = page.get_by_role('dialog', name='Файл для сдачи')
            expect(dialog.get_by_role('textbox', name='Команда')).to_have_value(config.get('team_name') or 'SetW')
            with page.expect_response(lambda r: r.url.startswith(base + '/api/submission/export?')) as preview_response:
                dialog.get_by_role('button', name='Подготовить файл').click()
            assert preview_response.value.status == 200, preview_response.value.text()
            preview = preview_response.value.json()
            expect(dialog.locator('.submission-totals dd')).to_have_text(['10', '1', '9'])
            with page.expect_download() as pending_download:
                dialog.get_by_role('button', name='Скачать results.json').click()
            downloaded = json.loads(Path(pending_download.value.path()).read_text(encoding='utf-8'))
            assert downloaded == preview and downloaded['schema_version'] == '1.0'
            assert {row['conversation_id'] for row in downloaded['results']} == {
                row['conversation_id'] for row in dataset['data']['records']
            }
            checks.append('submission_preview_and_download_use_the_same_complete_snapshot')

            voices = json.loads((ROOT/'fixtures/readiness/voice-dataset.json').read_text(encoding='utf-8'))
            voices['data']['dataset_group_id'] = group + '-VOICE'
            # Keep this record selected when import selects the group's first ID.
            voices['data']['records'][0]['conversation_id'] = '000-audio-' + uuid4().hex[:8]
            voices['data']['records'][0]['audio_url'] = 'https://example.invalid/synthetic-player'
            upload(voices)
            first = voices['data']['records'][0]['conversation_id']
            page.locator('.conversation-item').filter(has_text=first).click()
            expect(page.get_by_role('region', name='Аудиозапись звонка')).to_be_visible()
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
            check_audio_source_refresh(page, voices, upload, audio, out)
            checks.extend(['reimport_loaded_audio_source_resets_player', 'reimport_pending_audio_source_discards_obsolete_response'])
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
