"""Browser integration smoke test against an explicitly isolated test-double API.

Requires a server whose /api/config mode is 'test'. Never run against the user's
real dataset/model server. This verifies application wiring, not AI accuracy.
"""
from __future__ import annotations

from select_control import choose_select, expect_select_value, select_control
import argparse
from copy import deepcopy
import json
from pathlib import Path
import re
import time
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8001")
    args = parser.parse_args()
    base = args.url.rstrip("/")
    if urlsplit(base).hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise SystemExit("Refusing a non-local test server")
    api = httpx.Client(base_url=base, timeout=10)
    config = api.get("/api/config").raise_for_status().json()
    if config.get("mode") != "test":
        raise SystemExit("Refusing mutations: /api/config mode must be test")
    token = uuid4().hex[:8]
    dataset = json.loads((ROOT / "fixtures" / "synthetic_dev.json").read_text(encoding="utf-8"))
    group = "BROWSER_" + token
    dataset["data"]["dataset_group_id"] = group
    for record in dataset["data"]["records"]:
        record["conversation_id"] = f"browser-{token}-{record['language'].lower()}"
    conversation_id = f"browser-{token}-ru"
    errors = []
    checks = []
    (ROOT / "scratch").mkdir(exist_ok=True)

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
        except Exception:
            browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000}, reduced_motion="reduce")
        page.set_default_timeout(12000)
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            page.goto(base)
            page.wait_for_load_state("networkidle")
            expect(page.get_by_role("heading", level=1, name=re.compile(r"^Диалоги"))).to_be_visible()

            def import_dataset(value):
                with page.expect_response(lambda r: r.request.method == "POST" and r.url.endswith("/api/datasets/import")) as response:
                    page.get_by_label("Выбрать JSON с диалогами").set_input_files({
                        "name": "browser-new-dataset.json", "mimeType": "application/json",
                        "buffer": json.dumps(value, ensure_ascii=False).encode("utf-8")})
                assert response.value.status == 200, response.value.text()
                return response.value.json()

            imported = import_dataset(dataset)
            assert imported["imported"] == 3
            choose_select(select_control(page, "Версия чек-листа"), json.dumps(["synthetic-checklist", 1], separators=(",", ":")))
            page.get_by_role("button").filter(has_text=conversation_id).click()
            expect(page.locator(".conversation-context strong")).to_have_text(conversation_id)
            expect(page.locator(f'[id="message-{conversation_id}:0001"]')).to_be_visible()
            checks.append("synthetic_json_import_and_transcript")

            def analyze():
                with page.expect_response(lambda r: r.request.method == "POST" and r.url.endswith("/api/evaluation-jobs")) as response:
                    page.get_by_role("button", name=re.compile("^Оценить (диалог|снова)$")).click()
                assert response.value.status == 202, response.value.text()
                job_id = response.value.json()["job_id"]
                deadline = time.monotonic() + 12
                while time.monotonic() < deadline:
                    job = api.get(f"/api/evaluation-jobs/{job_id}").raise_for_status().json()
                    if job["status"] in {"completed", "failed"}:
                        break
                    page.wait_for_timeout(100)
                assert job["status"] == "completed", job
                value = api.get(f"/api/evaluations/{job['evaluation_id']}").raise_for_status().json()
                expect(page.locator(".aq-run-details").last).to_contain_text(value["evaluation_id"])
                return value

            original = analyze()
            assert original["revision"] == 1
            assert original["results"][0]["answer"] is False
            assert original["score"]["total"] < 100
            checks.append("real_job_endpoint_with_explicit_test_double")

            quote_criterion = original["checklist"]["criteria"][1]["criterion_id"]
            page.locator(f'[id="criterion-{quote_criterion}"]').click()
            page.locator(".aq-evidence").first.click()
            expect(page.locator(".message.highlighted")).to_have_attribute("id", f"message-{conversation_id}:0001")
            checks.append("evidence_navigation_and_highlight")

            first_criterion = original["checklist"]["criteria"][0]["criterion_id"]
            page.locator(f'[id="criterion-{first_criterion}"]').click()
            page.get_by_role("button", name="Оспорить оценку", exact=True).click()
            page.get_by_placeholder("Сошлитесь на реплику или правило чек-листа").fill("Учебная проверка: оператор поздоровался в реплике2.")
            with page.expect_response(lambda r: r.request.method == "POST" and r.url.endswith("/appeals")) as response:
                page.get_by_role("button", name="Отправить апелляцию", exact=True).click()
            assert response.value.status == 201
            page.get_by_role("button", name="Решение проверяющего", exact=True).click()
            choose_select(select_control(page, "Решение"), "replace")
            choose_select(select_control(page, "Новый ответ"), "true")
            page.get_by_label(re.compile("^Объяснение нового результата")).fill("Оператор произнёс приветствие, подтверждённое цитатой.")
            choose_select(select_control(page, "Доказательство"), "quote")
            choose_select(select_control(page, "Реплика"), f"{conversation_id}:0001")
            page.get_by_placeholder("Обязательно для истории пересмотра").fill("Исправлен тестовый ответ; сохранена исходная оценка.")
            with page.expect_response(lambda r: r.request.method == "POST" and r.url.endswith("/decision")) as response:
                page.get_by_role("button", name="Сохранить решение и пересчитать", exact=True).click()
            assert response.value.status == 200, response.value.text()
            revised = response.value.json()
            assert revised["revision"] == 2 and revised["score"]["total"] == 100
            assert revised["original_results"] == original["original_results"]
            assert revised["original_score"] == original["original_score"]
            assert revised["appeals"][0]["status"] == "resolved"
            expect(page.locator(".aq-run-details").last).to_contain_text("Ревизия2")
            checks.append("appeal_replacement_recalculation_and_immutable_original")
            page.screenshot(path=str(ROOT / "scratch" / "ui-test-desktop.png"), full_page=True)
            page.set_viewport_size({"width": 390, "height": 844})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "Mobile horizontal overflow"
            page.screenshot(path=str(ROOT / "scratch" / "ui-test-mobile.png"), full_page=True)
            page.set_viewport_size({"width": 1440, "height": 1000})

            page.get_by_role("button", name="Чек-листы", exact=True).click()
            page.get_by_role("button", name="Добавить критерий", exact=True).click()
            title = "Новый критерий " + token
            page.get_by_label(re.compile("^Название критерия")).last.fill(title)
            page.get_by_label(re.compile("^Инструкция для оценки")).last.fill("Определите, назвал ли оператор своё имя. Ответ true или false; отсутствие проверяйте по всему диалогу.")
            with page.expect_response(lambda r: r.request.method == "POST" and r.url.endswith("/versions")) as response:
                page.get_by_role("button", name="Сохранить новую версию", exact=True).click()
            assert response.value.status == 201, response.value.text()
            version = response.value.json()
            assert version["version"] >= 2 and len(version["criteria"]) == 3
            criterion_id = version["criteria"][-1]["criterion_id"]
            page.get_by_role("button", name="Диалоги", exact=True).click()
            expect_select_value(select_control(page, "Версия чек-листа"), json.dumps([version["checklist_id"], version["version"]], separators=(",", ":")))
            rerun = analyze()
            assert rerun["checklist"]["version"] == version["version"]
            assert criterion_id in {r["criterion_id"] for r in rerun["results"]}
            persisted = api.get(f"/api/evaluations/{original['evaluation_id']}").raise_for_status().json()
            assert persisted["checklist"]["version"] == 1 and persisted["score"]["total"] == 100
            checks.append("editor_new_criterion_new_version_applied_and_history_preserved")

            unseen = deepcopy(dataset)
            unseen["data"]["dataset_group_id"] = "UNSEEN_" + token
            unseen["data"]["records"] = [deepcopy(dataset["data"]["records"][0])]
            unseen["data"]["total_records"] = 1
            unseen_id = "unseen-" + token
            unseen["data"]["records"][0]["conversation_id"] = unseen_id
            unseen["data"]["records"][0]["transcript"][1]["text"] = "Здравствуйте! Я новый оператор. Это новая проверка интерфейса."
            assert import_dataset(unseen)["imported"] == 1
            page.get_by_role("button").filter(has_text=unseen_id).click()
            expect(page.locator(f'[id="message-{unseen_id}:0001"]')).to_be_visible()
            unseen_result = analyze()
            assert unseen_result["conversation"]["conversation_id"] == unseen_id
            assert "новый оператор" in unseen_result["conversation"]["transcript"][1]["text"]
            assert len(unseen_result["results"]) == 3
            checks.append("previously_unseen_json_uses_current_criteria")

            with page.expect_download() as download_info:
                page.get_by_role("button", name="Скачать выбранную оценку", exact=True).click()
            download = download_info.value
            export_path = ROOT / "scratch" / "browser-smoke-export.json"
            download.save_as(str(export_path))
            exported = json.loads(export_path.read_text(encoding="utf-8"))
            assert exported["evaluation_id"] == unseen_result["evaluation_id"]
            assert exported["conversation"]["conversation_id"] == unseen_id
            checks.append("json_export_download")
            assert not errors, errors
            output = {"status": "passed", "mode": "isolated_test_double_not_AI_quality",
                      "checks": checks, "group_id": group, "revision_before": 1, "revision_after": 2,
                      "score_before": original["score"]["total"], "score_after": revised["score"]["total"],
                      "new_checklist_version": version["version"], "page_errors": errors}
            (ROOT / "scratch" / "browser-smoke-result.json").write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(output, ensure_ascii=False, indent=2))
        except Exception:
            page.screenshot(path=str(ROOT / "scratch" / "browser-smoke-failure.png"), full_page=True)
            raise
        finally:
            browser.close()
            api.close()


if __name__ == "__main__":
    main()
