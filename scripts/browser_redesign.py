"""Verify the list/detail redesign on an explicit local test-double API only.

Imports synthetic chats, checks the new navigation and actual API workflows,
and captures screenshots. This does not measure AI quality or call a model.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import re
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from playwright.sync_api import expect, sync_playwright

from select_control import choose_select, expect_select_value, select_control

ROOT = Path(__file__).resolve().parents[1]
VIEWPORTS = ((1366, 768), (1024, 900), (700, 850), (494, 780), (390, 844), (320, 740))


def capture_current(base, group, api):
    """Refresh screenshots after cosmetic fixes, without importing or evaluating."""
    rows = api.get("/api/conversations").raise_for_status().json()
    rows = [row for row in rows if row["group_id"] == group]
    assert rows, "Capture group not found"
    cid = sorted(rows, key=lambda row: row["conversation_id"])[0]["conversation_id"]
    history = api.get("/api/evaluations", params={"group_id": group, "conversation_id": cid}).raise_for_status().json()
    evaluation = history[0]
    shots = ROOT / "docs/screenshots"
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1366, "height": 768}, reduced_motion="reduce")
        page.set_default_timeout(15000)
        page.goto(base, wait_until="networkidle")
        page.get_by_role("button", name=re.compile("^Фильтры")).click()
        choose_select(select_control(page, "Набор"), group)
        page.get_by_role("button", name=re.compile("^Фильтры")).click()
        for width, height in ((1366, 768), (494, 780), (390, 844)):
            page.set_viewport_size({"width": width, "height": height})
            page.screenshot(path=str(shots / f"redesign-index-{width}.png"), full_page=True)
        page.set_viewport_size({"width": 1366, "height": 768})
        page.get_by_role("button", name=f"Открыть диалог {cid} из {group}", exact=True).click()
        expect(page.locator(".aq-panel")).to_be_visible()
        page.screenshot(path=str(shots / "redesign-review-1366.png"), full_page=True)
        source = next(result for result in evaluation["results"] if result["evidence"])
        control = page.locator(f'[id="criterion-{source["criterion_id"]}"]')
        if control.get_attribute("aria-expanded") != "true": control.click()
        page.locator(".aq-evidence").first.click()
        expect(page.locator(".message.highlighted")).to_be_visible()
        page.screenshot(path=str(shots / "redesign-evidence-1366.png"), full_page=True)
        page.set_viewport_size({"width": 390, "height": 844})
        page.reload(wait_until="networkidle")
        page.get_by_role("tab", name="Оценка", exact=True).click()
        page.screenshot(path=str(shots / "redesign-review-mobile-390.png"), full_page=True)
        page.set_viewport_size({"width": 1366, "height": 768})
        page.get_by_role("button", name="Чек-листы", exact=True).click()
        page.screenshot(path=str(shots / "redesign-checklists-1366.png"), full_page=False)
        page.get_by_role("button", name="Диалоги", exact=True).click()
        page.get_by_role("button", name="Файл для сдачи", exact=True).click()
        dialog = page.get_by_role("dialog", name="Файл для сдачи", exact=True)
        dialog.get_by_role("button", name="Подготовить файл", exact=True).click()
        expect(dialog.get_by_role("heading", name="Файл готов к скачиванию")).to_be_visible()
        page.screenshot(path=str(shots / "redesign-export-1366.png"), full_page=False)
        browser.close()
    print(json.dumps({"status": "recaptured", "group": group, "mutations": 0, "paid_model_calls": 0}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8002")
    parser.add_argument("--capture-group", help="Recapture an existing synthetic group, without mutations")
    args = parser.parse_args()
    base = args.url.rstrip("/")
    if urlsplit(base).hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise SystemExit("Refusing a non-local API")
    api = httpx.Client(base_url=base, timeout=15)
    if api.get("/api/config").raise_for_status().json().get("mode") != "test":
        raise SystemExit("Refusing mutations: /api/config mode must be test")
    if args.capture_group:
        capture_current(base, args.capture_group, api)
        api.close()
        return
    token = uuid4().hex[:6]
    group = "REDESIGN-" + token
    dataset = json.loads((ROOT / "fixtures/readiness/chats-dataset.json").read_text(encoding="utf-8"))
    dataset["data"]["dataset_group_id"] = group
    for index, record in enumerate(dataset["data"]["records"]):
        record["conversation_id"] = f"review-{index + 1:03}-{token}"
    cid = dataset["data"]["records"][0]["conversation_id"]
    # Long source, including Kazakh letters, must remain readable and focusable.
    for index in range(12):
        dataset["data"]["records"][0]["transcript"].insert(-1, {
            "speaker": "client" if index % 2 else "operator", "timestamp": f"01:{index:02}",
            "text": "Учебная реплика. Проверяем историю операций и формирование брокерского отчёта. Қазақша әріптер: ә ғ қ ң ө ұ ү һ і.",
        })
    shots = ROOT / "docs/screenshots"
    out = ROOT / "scratch" / ("redesign-" + token)
    shots.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    checks, errors = [], []
    fixture_key = json.dumps(["synthetic-checklist", 1], separators=(",", ":"))

    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1366, "height": 768}, reduced_motion="reduce")
        page.set_default_timeout(15000)
        page.on("pageerror", lambda error: errors.append(str(error)))

        def viewport(width, height):
            page.set_viewport_size({"width": width, "height": height})

        def no_overflow(label):
            overflow = page.evaluate("""() => ({page: document.documentElement.scrollWidth, viewport: innerWidth,
                panels: [...document.querySelectorAll('#transcript-panel,#assessment-panel,.conversation-index')]
                .filter(e => e.getClientRects().length).map(e => ({id:e.id || e.className,w:e.clientWidth,scroll:e.scrollWidth}))})""")
            assert overflow["page"] <= overflow["viewport"] + 1, (label, overflow)
            assert all(x["scroll"] <= x["w"] + 1 for x in overflow["panels"]), (label, overflow)

        def filters(opened):
            control = page.get_by_role("button", name=re.compile(r"^Фильтры"))
            if (control.get_attribute("aria-expanded") == "true") != opened:
                control.click()

        def choose_group():
            filters(True)
            choose_select(select_control(page, "Набор"), group)
            filters(False)

        def open_detail(conversation=cid):
            page.get_by_role("button", name=f"Открыть диалог {conversation} из {group}", exact=True).click()
            expect(page.locator("#conversation-title")).to_have_text(conversation)

        def show_panel(name):
            control = page.get_by_role("tab", name=name, exact=True)
            if control.count() and control.is_visible():
                control.click()
                expect(control).to_have_attribute("aria-selected", "true")

        def expand(criterion):
            control = page.locator(f'[id="criterion-{criterion}"]')
            if control.get_attribute("aria-expanded") != "true":
                control.click()
            return control

        def launch(checklist=fixture_key):
            dialog = page.get_by_role("dialog", name="Запуск оценки", exact=True)
            expect(dialog).to_be_visible()
            choose_select(select_control(dialog, "Версия чек-листа"), checklist)
            with page.expect_response(lambda r: r.request.method == "POST" and r.url.endswith("/api/evaluation-jobs")) as pending:
                dialog.get_by_role("button", name="Начать оценку", exact=True).click()
            assert pending.value.status == 202, pending.value.text()
            expect(page.locator(".batch-banner")).to_contain_text("Обработка завершена", timeout=60000)
            expect(page.locator(".job-errors")).to_have_count(0)

        def screenshot(name):
            # Fixed dialogs and sticky save controls belong to the visible viewport.
            # Full-page stitching would place those controls over unrelated content.
            page.screenshot(path=str(shots / f"redesign-{name}.png"), full_page=not name.startswith(("checklists-", "export-")))

        try:
            page.route("**/api/conversations", lambda route: route.fulfill(status=503, json={"detail": "Synthetic initial list failure"}))
            page.goto(base, wait_until="networkidle")
            expect(page.get_by_role("heading", name="Рабочая область недоступна", exact=True)).to_be_visible()
            page.unroute("**/api/conversations")
            page.get_by_role("button", name="Повторить загрузку", exact=True).click()
            expect(page.get_by_role("heading", name="Рабочая область недоступна", exact=True)).to_have_count(0)
            checks.append("initial_list_503_and_user_retry_recover")
            with page.expect_response(lambda r: r.request.method == "POST" and r.url.endswith("/api/datasets/import")) as imported:
                page.get_by_label("Выбрать JSON с диалогами").set_input_files({"name": "synthetic-redesign.json", "mimeType": "application/json", "buffer": json.dumps(dataset, ensure_ascii=False).encode("utf-8")})
            assert imported.value.status == 200, imported.value.text()
            assert imported.value.json()["imported"] == 10
            expect(page.locator(".conversation-index")).to_be_visible()
            choose_group()
            expect(page.locator(".ci-table tbody tr")).to_have_count(10)
            checks.append("synthetic_json_import_opens_full_index")

            page.goto(base + "#/dialogue/%E0%A4%A", wait_until="networkidle")
            expect(page.locator(".conversation-index")).to_be_visible()
            choose_group()
            checks.append("malformed_route_recovers_to_index_without_crash")

            search = page.get_by_role("searchbox", name="Поиск диалога", exact=True)
            search.fill("no-such-dialogue-" + token)
            expect(page.get_by_role("heading", name="Диалоги не найдены")).to_be_visible()
            search.fill(cid)
            expect(page.locator(".ci-table tbody tr")).to_have_count(1)
            page.get_by_role("checkbox", name=f"Выбрать {cid} из {group}", exact=True).check()
            expect(page.locator(".ci-selection")).to_contain_text("Выбрано 1")
            search.fill("")
            expect(page.locator(".ci-selection")).to_have_count(0)
            expect(page.locator(".ci-table tbody tr")).to_have_count(10)
            checks.append("search_empty_result_and_selection_reset_on_filter_change")

            page.get_by_role("checkbox", name="Выбрать все диалоги в выборке (10)", exact=True).check()
            page.get_by_role("button", name="Оценить выбранные", exact=True).click()
            expect(page.get_by_role("dialog", name="Запуск оценки")).to_contain_text("Будут оценены 10 диалогов")
            launch()
            expect(page.locator(".batch-banner")).to_contain_text("Обработано 10 из 10")
            page.get_by_role("button", name="Снять выбор", exact=True).click()
            page.get_by_role("button", name="Скрыть прогресс", exact=True).click()
            checks.append("selected_batch_run_configuration_and_ten_actual_results")
            views = page.get_by_role("group", name="Отбор по состоянию оценки")
            views.get_by_role("button", name=re.compile(r"^На проверку")).click()
            expect(page.locator(".ci-table tbody tr")).to_have_count(2)
            views.get_by_role("button", name=re.compile(r"^Без оценки")).click()
            expect(page.get_by_role("heading", name="Диалоги не найдены")).to_be_visible()
            views.get_by_role("button", name=re.compile(r"^Грубые ошибки")).click()
            expect(page.get_by_role("heading", name="Диалоги не найдены")).to_be_visible()
            views.get_by_role("button", name=re.compile(r"^Все диалоги")).click()
            expect(page.locator(".ci-table tbody tr")).to_have_count(10)
            checks.append("state_views_use_actual_complete_incomplete_and_unrated_counts")

            for width, height in VIEWPORTS:
                viewport(width, height)
                no_overflow(f"index-{width}")
                if width in (1366, 494, 390): screenshot(f"index-{width}")
                checks.append(f"index_layout_{width}")
            viewport(1366, 768)
            page.get_by_role("button", name="Диалог", exact=True).click()
            page.get_by_role("button", name="Диалог", exact=True).click()
            ordered_ids = page.locator(".ci-open-dialogue").evaluate_all("els => els.map(e => e.getAttribute('aria-label'))")
            page.locator(".ci-open-dialogue").first.click()
            first_sorted = page.locator("#conversation-title").inner_text()
            page.get_by_role("button", name="Следующий диалог", exact=True).click()
            second_sorted = page.locator("#conversation-title").inner_text()
            assert first_sorted in ordered_ids[0] and second_sorted in ordered_ids[1], (first_sorted, second_sorted, ordered_ids)
            page.get_by_role("button", name="Все диалоги", exact=True).click()
            expect(page.locator(".ci-open-dialogue").first).to_have_attribute("aria-label", ordered_ids[0])
            page.get_by_role("button", name="Диалог", exact=True).click()
            checks.append("sort_persists_across_detail_and_next_uses_visible_order")
            open_detail()
            expect(page).to_have_url(re.compile(r"#/dialogue/"))
            original_hash = page.url
            page.get_by_role("link", name="Перейти к рабочей области", exact=True).focus()
            page.keyboard.press("Enter")
            expect(page.locator("#main-content")).to_be_focused()
            assert page.url == original_hash, "Skip link changed the route"
            checks.append("skip_link_moves_focus_without_changing_route")
            page.get_by_role("button", name="Следующий диалог", exact=True).click()
            expect(page.locator("#conversation-title")).not_to_have_text(cid)
            page.go_back()
            expect(page.locator("#conversation-title")).to_have_text(cid)
            page.reload(wait_until="networkidle")
            expect(page.locator("#conversation-title")).to_have_text(cid)
            assert page.url == original_hash
            checks.append("detail_next_browser_back_and_deep_link_reload")

            page.route("**/api/conversations/**", lambda route: route.fulfill(status=503, json={"detail": "Synthetic transcript failure"}))
            page.reload(wait_until="networkidle")
            expect(page.locator("#transcript-panel")).to_contain_text("Транскрипт недоступен")
            page.unroute("**/api/conversations/**")
            page.locator("#transcript-panel").get_by_role("button", name="Повторить загрузку", exact=True).click()
            expect(page.locator("#transcript-panel .message").first).to_be_visible()
            expect(page.locator("#transcript-panel")).not_to_contain_text("Транскрипт недоступен")
            checks.append("detail_503_and_user_retry_recover_saved_context")

            evaluations = api.get("/api/evaluations", params={"conversation_id": cid, "group_id": group}).raise_for_status().json()
            original = evaluations[0]
            if "results" not in original:
                original = api.get("/api/evaluations/" + original["evaluation_id"]).raise_for_status().json()
            greeting = original["checklist"]["criteria"][0]["criterion_id"]
            quote = original["checklist"]["criteria"][1]["criterion_id"]
            quote_result = next(row for row in original["results"] if row["criterion_id"] == quote)
            quote_id = quote_result["evidence"][0]["message_id"]

            for width, height in VIEWPORTS:
                viewport(width, height)
                show_panel("Разговор")
                expect(page.locator("#transcript-panel")).to_be_visible()
                no_overflow(f"detail-transcript-{width}")
                if width <= 700:
                    expect(page.locator("#assessment-panel")).to_be_hidden()
                    show_panel("Оценка")
                    expect(page.locator("#assessment-panel")).to_be_visible()
                    expect(page.locator("#transcript-panel")).to_be_hidden()
                    no_overflow(f"detail-assessment-{width}")
                    assert page.get_by_role("tab", name="Оценка", exact=True).bounding_box()["height"] >= 44
                if width == 1366: screenshot("review-1366")
                if width == 390: screenshot("review-mobile-390")
                checks.append(f"detail_layout_{width}")

            viewport(390, 844)
            show_panel("Оценка")
            page.get_by_role("tab", name="Оценка", exact=True).focus()
            page.keyboard.press("Home")
            expect(page.get_by_role("tab", name="Разговор", exact=True)).to_be_focused()
            page.keyboard.press("End")
            expect(page.get_by_role("tab", name="Оценка", exact=True)).to_be_focused()
            expand(quote)
            page.locator(".aq-evidence").first.click()
            target = page.locator(f'[id="message-{quote_id}"]')
            expect(target).to_be_focused()
            expect(target.locator("mark")).to_have_text(quote_result["evidence"][0]["quote"])
            expect(page.get_by_role("tab", name="Разговор", exact=True)).to_have_attribute("aria-selected", "true")
            page.get_by_role("button", name="К критерию", exact=True).click()
            expect(page.locator(f'[id="criterion-{quote}"]')).to_be_focused()
            checks.append("mobile_keyboard_tabs_exact_quote_focus_and_return")
            expand(greeting)
            page.get_by_role("button", name=re.compile("Проверенный участок")).click()
            expect(page.locator(".message.highlighted")).to_have_count(len(original["conversation"]["transcript"]))
            expect(page.locator(".range-start")).to_have_count(1)
            expect(page.locator(".range-end")).to_have_count(1)
            page.get_by_role("button", name="К критерию", exact=True).click()
            checks.append("checked_span_preserves_whole_source_and_boundaries")

            viewport(1366, 768)
            page.get_by_role("button", name="Оспорить оценку", exact=True).click()
            comment = page.get_by_placeholder("Сошлитесь на реплику или правило чек-листа")
            draft = "Учебная проверка: приветствие видно в реплике оператора."
            comment.fill(draft)
            page.get_by_role("button", name="Все диалоги", exact=True).click()
            choose_group()
            open_detail()
            expand(greeting)
            expect(comment).to_have_value(draft)
            comment.press("Escape")
            expect(page.get_by_role("button", name="Оспорить оценку", exact=True)).to_be_focused()
            page.get_by_role("button", name="Оспорить оценку", exact=True).click()
            expect(comment).to_have_value(draft)
            checks.append("appeal_draft_survives_index_navigation_and_escape")
            with page.expect_response(lambda r: r.request.method == "POST" and r.url.endswith("/appeals")) as appeal:
                page.get_by_role("button", name="Отправить апелляцию", exact=True).click()
            assert appeal.value.status == 201
            page.get_by_role("button", name="Решение проверяющего", exact=True).click()
            choose_select(select_control(page, "Решение"), "replace")
            choose_select(select_control(page, "Новый ответ"), "true")
            page.get_by_label(re.compile("^Объяснение нового результата")).fill("Приветствие присутствует в исходной реплике оператора.")
            choose_select(select_control(page, "Доказательство"), "quote")
            choose_select(select_control(page, "Реплика"), quote_id)
            page.get_by_placeholder("Обязательно для истории пересмотра").fill("Исправлен тестовый ответ по цитате.")
            with page.expect_response(lambda r: r.request.method == "POST" and r.url.endswith("/decision")) as decision:
                page.get_by_role("button", name="Сохранить решение и пересчитать", exact=True).click()
            assert decision.value.status == 200, decision.value.text()
            revised = decision.value.json()
            assert revised["original_score"] == original["original_score"]
            assert revised["original_results"] == original["original_results"]
            assert revised["score"]["total"] > original["score"]["total"]
            expect(page.get_by_role("group", name="Версия результата")).to_be_visible()
            screenshot("evidence-1366")
            checks.append("appeal_decision_recalculates_and_preserves_original")

            page.get_by_role("button", name="Чек-листы", exact=True).click()
            choose_select(select_control(page, "Версия чек-листа"), fixture_key)
            page.get_by_role("button", name="Добавить критерий", exact=True).click()
            criterion_title = "Представление оператора " + token
            page.get_by_label(re.compile("^Название критерия")).fill(criterion_title)
            page.get_by_role("spinbutton", name="Вес", exact=True).fill("3")
            page.get_by_label(re.compile("^Инструкция для оценки")).fill("Проверьте, назвал ли оператор имя. Подтвердите ответ точной цитатой.")
            page.get_by_role("button", name="Диалоги", exact=True).click()
            page.get_by_role("button", name="Чек-листы", exact=True).click()
            expect(page.get_by_label(re.compile("^Название критерия"))).to_have_value(criterion_title)
            expect(page.get_by_role("spinbutton", name="Вес", exact=True)).to_have_value("3")
            with page.expect_response(lambda r: r.request.method == "POST" and r.url.endswith("/versions")) as saved:
                page.get_by_role("button", name="Сохранить новую версию", exact=True).click()
            assert saved.value.status == 201, saved.value.text()
            version = saved.value.json()
            version_key = json.dumps([version["checklist_id"], version["version"]], separators=(",", ":"))
            picker = select_control(page, "Версия чек-листа")
            choose_select(picker, fixture_key)
            page.get_by_role("spinbutton", name="Вес", exact=True).fill("7")
            choose_select(picker, version_key)
            choose_select(picker, fixture_key)
            expect(page.get_by_role("spinbutton", name="Вес", exact=True)).to_have_value("7")
            choose_select(picker, version_key)
            screenshot("checklists-1366")
            for width, height in VIEWPORTS:
                viewport(width, height)
                no_overflow(f"checklists-{width}")
            checks.append("checklist_draft_tab_version_roundtrip_save_and_responsive_layout")

            official = json.loads((ROOT / "fixtures/checklist.official.json").read_text(encoding="utf-8"))
            existing = api.get("/api/checklists").raise_for_status().json()
            if not any(x["checklist_id"] == official["checklist_id"] for x in existing):
                api.post("/api/checklists", json=official).raise_for_status()
            viewport(1366, 768)
            page.get_by_role("button", name="Диалоги", exact=True).click()
            page.reload(wait_until="networkidle")
            choose_group()
            open_detail()
            page.get_by_role("button", name="Оценить снова", exact=True).click()
            launch(json.dumps([official["checklist_id"], official["version"]], separators=(",", ":")))
            page.get_by_role("button", name="Все диалоги", exact=True).click()
            page.get_by_role("button", name="Файл для сдачи", exact=True).click()
            export = page.get_by_role("dialog", name="Файл для сдачи", exact=True)
            with page.expect_response(lambda r: r.url.startswith(base + "/api/submission/export?")) as preview_response:
                export.get_by_role("button", name="Подготовить файл", exact=True).click()
            assert preview_response.value.status == 200, preview_response.value.text()
            preview = preview_response.value.json()
            expect(export.locator(".submission-totals dd")).to_have_text(["10", "1", "9"])
            screenshot("export-1366")
            for width, height in ((494, 780), (390, 844), (320, 740)):
                viewport(width, height)
                no_overflow(f"export-{width}")
            with page.expect_download() as download:
                export.get_by_role("button", name="Скачать results.json", exact=True).click()
            downloaded = json.loads(Path(download.value.path()).read_text(encoding="utf-8"))
            assert downloaded == preview and downloaded["schema_version"] == "1.0"
            assert {row["conversation_id"] for row in downloaded["results"]} == {row["conversation_id"] for row in dataset["data"]["records"]}
            checks.append("official_export_preview_and_download_match_actual_document")

            assert not errors, errors
            result = {"status": "passed", "mode": "isolated_test_double_not_AI_quality", "paid_model_calls": 0,
                      "checks": checks, "page_errors": errors, "group_id": group,
                      "screenshots": [p.name for p in shots.glob("redesign-*.png")]}
            (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            (ROOT / "scratch/browser-redesign-result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(result, ensure_ascii=False, indent=2))
        except Exception:
            page.screenshot(path=str(out / "failure.png"), full_page=True)
            print(f"Failure screenshot: {out / 'failure.png'}")
            print("Completed checks: " + json.dumps(checks))
            raise
        finally:
            browser.close()
            api.close()


if __name__ == "__main__":
    main()
