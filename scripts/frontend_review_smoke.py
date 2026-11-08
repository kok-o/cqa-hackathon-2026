"""Responsive review regression against the isolated browser-test API.

This suite uses synthetic records and the explicit test evaluator. It tests UI
navigation and persistence, never model accuracy. Start the server described in
docs/FRONTEND_QA.md before running this script.
"""
from __future__ import annotations

from select_control import choose_select, expect_select_value, select_control, set_filters_open
import argparse
from copy import deepcopy
import json
from pathlib import Path
import re
import time
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
VIEWPORTS = ((1440, 1000), (1024, 900), (390, 844), (320, 740))


def seed(api: httpx.Client, token: str) -> tuple[str, str, dict]:
    dataset = json.loads((ROOT / 'fixtures/synthetic_dev.json').read_text(encoding='utf-8'))
    group = 'FRONTEND_' + token
    dataset['data']['dataset_group_id'] = group
    for record in dataset['data']['records']:
        record['conversation_id'] = f'frontend-{token}-{record["language"].lower()}'
    ru = dataset['data']['records'][0]
    ru['transcript'].insert(1, {'timestamp': '00:01', 'speaker': 'bot', 'text': 'Выберите тему обращения.'})
    # Long transcript exercises independent pane scrolling and source anchors.
    for index in range(20):
        ru['transcript'].insert(-1, {
            'timestamp': f'01:{index:02}', 'speaker': 'operator' if index % 2 else 'client',
            'text': f'Учебная реплика {index + 1}. ' + 'Проверяем историю операций и выгрузку брокерского отчёта. ' * 6,
        })
    api.post('/api/datasets/import', json={'dataset': dataset}).raise_for_status()
    job = api.post('/api/evaluation-jobs', json={
        'conversation_id': ru['conversation_id'], 'group_id': group,
        'checklist_id': 'synthetic-checklist', 'checklist_version': 1,
        'idempotency_key': uuid4().hex,
    }).raise_for_status().json()
    deadline = time.monotonic() + 15
    while job['status'] in ('queued', 'running') and time.monotonic() < deadline:
        time.sleep(0.1)
        job = api.get('/api/evaluation-jobs/' + job['job_id']).raise_for_status().json()
    assert job['status'] == 'completed', job
    evaluation = api.get('/api/evaluations/' + job['evaluation_id']).raise_for_status().json()
    # The shared mechanical evaluator emits quotes. Prepare a checked-span
    # result explicitly through the appeal API, so both evidence paths are
    # exercised without relying on a different private test server.
    greeting = deepcopy(evaluation['results'][0])
    greeting['evidence'] = []
    greeting['checked_span'] = {
        'start_message_id': evaluation['conversation']['transcript'][0]['message_id'],
        'end_message_id': evaluation['conversation']['transcript'][-1]['message_id'],
    }
    appeal = api.post('/api/evaluations/' + evaluation['evaluation_id'] + '/appeals', json={
        'criterion_id': greeting['criterion_id'], 'comment': 'Synthetic checked-span UI setup.',
        'idempotency_key': uuid4().hex,
    }).raise_for_status().json()
    evaluation = api.post('/api/appeals/' + appeal['appeal_id'] + '/decision', json={
        'action': 'replace', 'reason': 'Exercise the whole-transcript anchor in the isolated UI test.',
        'replacement_result': greeting, 'expected_revision': evaluation['revision'],
        'idempotency_key': uuid4().hex,
    }).raise_for_status().json()
    return group, ru['conversation_id'], evaluation


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8001')
    args = parser.parse_args()
    base = args.url.rstrip('/')
    if urlsplit(base).hostname not in {'127.0.0.1', 'localhost', '::1'}:
        raise SystemExit('Refusing a non-local test server')
    api = httpx.Client(base_url=base, timeout=15)
    if api.get('/api/config').raise_for_status().json().get('mode') != 'test':
        raise SystemExit('Refusing mutations: /api/config mode must be test')
    token = uuid4().hex[:8]
    group, conversation_id, evaluation = seed(api, token)
    output_dir = ROOT / 'scratch' / ('frontend-' + token)
    output_dir.mkdir(parents=True)
    checks: list[str] = []
    errors: list[str] = []
    greeting_id = evaluation['checklist']['criteria'][0]['criterion_id']
    quote_id = evaluation['checklist']['criteria'][1]['criterion_id']
    first_message = evaluation['conversation']['transcript'][0]['message_id']
    last_message = evaluation['conversation']['transcript'][-1]['message_id']
    quote_message = evaluation['results'][1]['evidence'][0]['message_id']

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(channel='chrome', headless=True)
        except Exception:
            browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 1440, 'height': 1000}, reduced_motion='reduce')
        page.set_default_timeout(12000)
        page.on('pageerror', lambda error: errors.append(str(error)))

        def select_tab(name: str) -> None:
            tab = page.get_by_role('tab', name=name, exact=True)
            if tab.count() and tab.is_visible():
                tab.click()
                expect(tab).to_have_attribute('aria-selected', 'true')

        def criterion_button(criterion_id: str):
            return page.locator(f'[id="criterion-{criterion_id}"]')

        def assert_no_overflow() -> None:
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'Horizontal page overflow'
            for selector in ('#queue-panel', '#transcript-panel', '#assessment-panel'):
                panel = page.locator(selector)
                if panel.is_visible():
                    if selector == '#queue-panel':
                        # The resize hit target deliberately straddles the pane
                        # border. Check content overflow, excluding only it.
                        assert panel.evaluate('''el => [...el.children]
                            .filter(child => !child.matches('.queue-resize-handle'))
                            .every(child => child.scrollWidth <= child.clientWidth + 1 &&
                                child.getBoundingClientRect().right <= el.getBoundingClientRect().right + 1)'''), f'Panel content overflow: {selector}'
                    else:
                        assert panel.evaluate('(el) => el.scrollWidth <= el.clientWidth + 1'), f'Panel overflow: {selector}'

        try:
            page.goto(base)
            page.wait_for_load_state('networkidle')
            expect(page.get_by_role('heading', level=1, name=re.compile(r'^Диалоги'))).to_be_visible()
            choose_select(select_control(page, 'Чек-лист'), json.dumps(['synthetic-checklist', 1], separators=(',', ':')))
            set_filters_open(page, True)
            choose_select(select_control(page, 'Набор'), group)
            set_filters_open(page, False)
            page.get_by_role('button').filter(has_text=conversation_id).click()
            expect(page.locator('#conversation-title')).to_have_text(conversation_id)
            expect(page.locator('.aq-run-details').last).to_contain_text(evaluation['evaluation_id'])
            expect(page.locator('.transcript-intro-toggle')).to_have_attribute('aria-expanded', 'false')
            checks.append('synthetic_long_transcript_and_saved_run')

            for width, height in VIEWPORTS:
                page.set_viewport_size({'width': width, 'height': height})
                if width < 768:
                    for name, panel in (('Очередь', 'queue-panel'), ('Диалог', 'transcript-panel'), ('Оценка', 'assessment-panel')):
                        select_tab(name)
                        expect(page.locator('#' + panel)).to_be_visible()
                        for other in ('queue-panel', 'transcript-panel', 'assessment-panel'):
                            if other != panel:
                                expect(page.locator('#' + other)).to_be_hidden()
                        bounds = page.get_by_role('tab', name=name, exact=True).bounding_box()
                        assert bounds and bounds['height'] >= 44, f'Small mobile target: {name}'
                        assert_no_overflow()
                    checks.append(f'mobile_single_panel_tabs_{width}')
                else:
                    expect(page.locator('#transcript-panel')).to_be_visible()
                    expect(page.locator('#assessment-panel')).to_be_visible()
                    assert_no_overflow()
                page.screenshot(path=str(output_dir / f'review-{width}.png'), full_page=True)
                checks.append(f'layout_{width}')

            page.set_viewport_size({'width': 390, 'height': 844})
            select_tab('Очередь')
            queue_tab = page.get_by_role('tab', name='Очередь', exact=True)
            queue_tab.focus()
            queue_tab.press('End')
            expect(page.get_by_role('tab', name='Оценка', exact=True)).to_be_focused()
            expect(page.get_by_role('tab', name='Оценка', exact=True)).to_have_attribute('aria-selected', 'true')
            page.get_by_role('tab', name='Оценка', exact=True).press('Home')
            expect(queue_tab).to_be_focused()
            expect(queue_tab).to_have_attribute('aria-selected', 'true')
            set_filters_open(page, True)
            select_control(page, 'Набор').press('Escape')
            expect(page.locator('#queue-filters')).to_have_count(0)
            expect(page.get_by_role('button', name=re.compile('^Фильтры'))).to_be_focused()
            checks.append('keyboard_mobile_tabs_and_escape_filter_focus_return')
            select_tab('Оценка')
            criterion_button(quote_id).click()
            page.locator('.aq-evidence').first.click()
            expect(page.get_by_role('tab', name='Диалог', exact=True)).to_have_attribute('aria-selected', 'true')
            quote = page.locator(f'[id="message-{quote_message}"]')
            expect(quote).to_have_class(re.compile('highlighted'))
            expect(quote).to_be_focused()
            expect(quote.locator('mark')).to_have_text(evaluation['results'][1]['evidence'][0]['quote'])
            page.get_by_role('button', name='К критерию', exact=True).click()
            expect(page.get_by_role('tab', name='Оценка', exact=True)).to_have_attribute('aria-selected', 'true')
            expect(criterion_button(quote_id)).to_be_focused()
            expect(criterion_button(quote_id)).to_have_attribute('aria-expanded', 'true')
            checks.append('mobile_quote_source_focus_and_return_to_same_criterion')

            criterion_button(greeting_id).click()
            page.get_by_role('button', name=re.compile('Проверенный участок')).click()
            expect(page.locator(f'[id="message-{first_message}"]')).to_have_class(re.compile('range-start'))
            expect(page.locator(f'[id="message-{last_message}"]')).to_have_class(re.compile('range-end'))
            expect(page.locator('.message.highlighted')).to_have_count(len(evaluation['conversation']['transcript']))
            expect(page.locator('.transcript-intro-toggle')).to_have_attribute('aria-expanded', 'true')
            page.get_by_role('button', name='К критерию', exact=True).click()
            expect(criterion_button(greeting_id)).to_be_focused()
            checks.append('checked_span_all_messages_and_boundary_highlight')
            page.screenshot(path=str(output_dir / 'mobile-evidence-return.png'), full_page=True)

            # A second reviewer advances the revision while this form is open.
            # Refresh must preserve the first reviewer's reason and recover.
            page.get_by_role('button', name='Оспорить оценку', exact=True).click()
            appeal_comment = page.get_by_placeholder('Сошлитесь на реплику или правило чек-листа')
            appeal_comment.fill('Учебный параллельный пересмотр.')
            criterion_button(quote_id).click()
            expect(appeal_comment).to_have_count(0)
            criterion_button(greeting_id).click()
            expect(appeal_comment).to_have_value('Учебный параллельный пересмотр.')
            criterion_button(greeting_id).click()
            expect(appeal_comment).to_have_count(0)
            criterion_button(greeting_id).click()
            expect(appeal_comment).to_have_value('Учебный параллельный пересмотр.')
            select_tab('Диалог')
            expect(appeal_comment).to_be_hidden()
            select_tab('Оценка')
            expect(appeal_comment).to_have_value('Учебный параллельный пересмотр.')
            checks.append('appeal_draft_survives_criterion_switch_collapse_and_mobile_tabs')
            history = select_control(page, 'История оценок')
            choose_select(history, '')
            choose_select(history, evaluation['evaluation_id'])
            criterion_button(greeting_id).click()
            expect(appeal_comment).to_have_value('Учебный параллельный пересмотр.')
            page.get_by_role('button', name='Чек-листы', exact=True).click()
            page.get_by_role('button', name='Диалоги', exact=True).click()
            criterion_button(greeting_id).click()
            expect(appeal_comment).to_have_value('Учебный параллельный пересмотр.')
            checks.append('appeal_draft_survives_history_and_workspace_navigation')
            appeal_comment.press('Escape')
            expect(page.get_by_role('button', name='Оспорить оценку', exact=True)).to_be_focused()
            page.get_by_role('button', name='Оспорить оценку', exact=True).click()
            expect(appeal_comment).to_have_value('Учебный параллельный пересмотр.')
            checks.append('escape_appeal_focus_return_and_draft_preserved')
            with page.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith('/appeals')) as response:
                page.get_by_role('button', name='Отправить апелляцию', exact=True).click()
            assert response.value.status == 201
            page.get_by_role('button', name='Решение проверяющего', exact=True).click()
            reason = 'Сохранить эту причину после конфликта ревизии.'
            reason_input = page.get_by_placeholder('Обязательно для истории пересмотра')
            reason_input.fill(reason)
            explanation = 'Черновик решения с новой цитатой сохраняется при навигации.'
            replacement_message = evaluation['conversation']['transcript'][3]['message_id']
            choose_select(select_control(page, 'Решение'), 'replace')
            choose_select(select_control(page, 'Новый ответ'), 'true')
            page.get_by_role('textbox', name=re.compile('^Объяснение нового результата')).fill(explanation)
            choose_select(select_control(page, 'Доказательство'), 'quote')
            choose_select(select_control(page, 'Реплика'), replacement_message)

            def assert_decision_draft() -> None:
                expect(reason_input).to_have_value(reason)
                expect_select_value(select_control(page, 'Решение'), 'replace')
                expect_select_value(select_control(page, 'Применимость'), 'applicable')
                expect_select_value(select_control(page, 'Новый ответ'), 'true')
                expect(page.get_by_role('textbox', name=re.compile('^Объяснение нового результата'))).to_have_value(explanation)
                expect_select_value(select_control(page, 'Доказательство'), 'quote')
                expect_select_value(select_control(page, 'Реплика'), replacement_message)

            criterion_button(quote_id).click()
            expect(reason_input).to_have_count(0)
            criterion_button(greeting_id).click()
            assert_decision_draft()
            criterion_button(greeting_id).click()
            expect(reason_input).to_have_count(0)
            criterion_button(greeting_id).click()
            assert_decision_draft()
            select_tab('Диалог')
            expect(reason_input).to_be_hidden()
            select_tab('Оценка')
            assert_decision_draft()
            reason_input.press('Escape')
            expect(reason_input).to_have_count(0)
            expect(page.get_by_role('button', name='Решение проверяющего', exact=True)).to_be_focused()
            page.get_by_role('button', name='Решение проверяющего', exact=True).click()
            assert_decision_draft()
            checks.append('decision_action_reason_answer_explanation_and_quote_survive_navigation_and_escape')
            competing = api.post('/api/evaluations/' + evaluation['evaluation_id'] + '/appeals', json={
                'criterion_id': quote_id, 'comment': 'Вторая учебная проверка', 'idempotency_key': uuid4().hex,
            }).raise_for_status().json()
            concurrent = api.post('/api/appeals/' + competing['appeal_id'] + '/decision', json={
                'action': 'uphold', 'reason': 'Изолированная проверка конфликта.',
                'replacement_result': None, 'expected_revision': evaluation['revision'], 'idempotency_key': uuid4().hex,
            }).raise_for_status().json()
            with page.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith('/decision')) as conflict:
                page.get_by_role('button', name='Сохранить решение и пересчитать', exact=True).click()
            assert conflict.value.status == 409
            page.get_by_role('button', name='Обновить данные оценки', exact=True).click()
            assert_decision_draft()
            with page.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith('/decision')) as resolved:
                page.get_by_role('button', name='Сохранить решение и пересчитать', exact=True).click()
            assert resolved.value.status == 200, resolved.value.text()
            latest = resolved.value.json()
            assert latest['revision'] == concurrent['revision'] + 1
            assert latest['original_results'] == evaluation['original_results']
            assert latest['original_score'] == evaluation['original_score']
            replacement = next(item for item in latest['results'] if item['criterion_id'] == greeting_id)
            assert replacement['answer'] is True and replacement['explanation'] == explanation
            assert replacement['evidence'][0]['message_id'] == replacement_message
            checks.append('conflict_409_refresh_preserves_reason_and_recovers')

            select_tab('Очередь')
            page.get_by_label('Поиск диалога', exact=True).fill('no-such-' + token)
            expect(page.get_by_text('Ничего не найдено', exact=True)).to_be_visible()
            page.locator('.conversation-list .small-empty').get_by_role('button', name='Сбросить фильтры', exact=True).click()
            set_filters_open(page, True)
            choose_select(select_control(page, 'Набор'), group)
            choose_select(select_control(page, 'Язык'), 'KZ')
            set_filters_open(page, False)
            page.get_by_role('button').filter(has_text=f'frontend-{token}-kz').click()
            select_tab('Диалог')
            expect(page.locator('#transcript-panel')).to_contain_text('Сәлеметсіз бе!')
            checks.append('empty_search_reset_and_kazakh_text')

            select_tab('Очередь')
            set_filters_open(page, True)
            choose_select(select_control(page, 'Язык'), 'EN')
            set_filters_open(page, False)
            page.get_by_role('button').filter(has_text=f'frontend-{token}-en').click()
            select_tab('Диалог')
            expect(page.locator('.conversation-context')).to_contain_text('Звонок')
            expect(page.locator('#transcript-panel .message')).to_have_count(4)
            expect(page.locator('audio')).to_have_count(0)
            expect(page.get_by_role('button', name='Открыть запись звонка', exact=True)).to_have_count(0)
            checks.append('voice_transcript_available_without_unprovided_audio_control')

            # The browser receives an invalid source file. No import request may
            # succeed and the currently selected conversation must survive.
            page.get_by_label('Выбрать JSON с диалогами').set_input_files({
                'name': 'invalid.json', 'mimeType': 'application/json', 'buffer': b'{broken json',
            })
            expect(page.get_by_role('alert')).to_contain_text('JSON')
            expect(page.locator('#conversation-title')).to_have_text(f'frontend-{token}-en')
            checks.append('invalid_json_keeps_current_conversation')

            page.emulate_media(reduced_motion='reduce')
            assert page.evaluate('matchMedia("(prefers-reduced-motion: reduce)").matches')
            durations = page.evaluate('''() => {
                const seconds = value => value.split(',').map(v => v.trim().endsWith('ms') ? parseFloat(v) / 1000 : parseFloat(v));
                return [...document.querySelectorAll('.message, .aq-panel, .filters')].flatMap(el => {
                    const style = getComputedStyle(el);
                    return [...seconds(style.transitionDuration), ...seconds(style.animationDuration)];
                });
            }''')
            assert durations and max(durations) <= 0.02, f'Reduced motion still animates: {durations}'
            checks.append('reduced_motion_effective_css_durations')
            assert not errors, errors
            result = {'status': 'passed', 'mode': 'isolated_test_double_not_AI_quality',
                      'checks': checks, 'page_errors': errors, 'group_id': group,
                      'screenshots': [p.name for p in output_dir.glob('*.png')]}
            (output_dir / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps(result, ensure_ascii=False, indent=2))
        except Exception:
            page.screenshot(path=str(output_dir / 'failure.png'), full_page=True)
            raise
        finally:
            browser.close()
            api.close()


if __name__ == '__main__':
    main()
