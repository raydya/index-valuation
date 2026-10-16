"""Check the live, read-only investment task panel with Playwright WebKit."""

import asyncio
import copy
import json
from pathlib import Path

import httpx
from playwright.async_api import async_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data/exports"
URL = "http://127.0.0.1:8000"
API = f"{URL}/api/scheduled-tasks"
TASK_IDS = {"erp", "portfolio_us", "index_us", "index_asia", "portfolio_asia"}
RUN_LABELS = {
    "never_run": "尚无执行记录",
    "queued": "已排队",
    "running": "执行中",
    "completed": "已完成",
    "finished": "已返回结果",
    "partial": "部分完成",
    "failed": "执行失败",
    "timed_out": "执行超时",
    "unknown": "结果待核实",
}
SCHEDULER_LABELS = {
    "enabled": "已启用",
    "running": "已启用 · 执行中",
    "disabled": "已停用",
    "not_installed": "未安装",
    "unknown": "调度待核实",
}
DATA_LABELS = {
    "available": "有可用数据",
    "partial": "部分完成",
    "paused": "暂停采集",
    "unknown": "待核实",
}


def validate_payload(payload):
    assert payload["timezone"] == "Asia/Shanghai"
    assert payload["checked_at"]
    assert len(payload["tasks"]) == 5
    assert {task["id"] for task in payload["tasks"]} == TASK_IDS


def task_row(page, task_id):
    assert task_id in TASK_IDS
    return page.locator(f'.task-row[data-task-id="{task_id}"]')


async def expand(row, opened=True):
    if await row.evaluate("element => element.open") != opened:
        await row.locator(".task-summary").click()


async def check_rows(page, snapshot):
    validate_payload(snapshot)
    await expect(page.locator(".task-row")).to_have_count(5)
    await expect(page.locator("#tasks-overview")).to_contain_text("5 项任务")
    await expect(page.locator("#tasks-checked-at")).to_contain_text("状态读取于")
    for task in snapshot["tasks"]:
        row = task_row(page, task["id"])
        await expect(row.locator(".task-name")).to_have_text(task["name"])
        await expect(row.locator(".task-plan-time")).to_have_text(task["schedule_text"])
        await expect(row.locator(".task-plan .task-badge")).to_have_text(
            SCHEDULER_LABELS[task["scheduler"]["state"]]
        )
        await expect(row.locator(".task-execution .task-badge")).to_have_text(
            RUN_LABELS[task["last_run"]["status"]]
        )
        if task["last_run"]["summary"]:
            assert await row.locator(".task-excerpt").text_content() == task["last_run"]["summary"]


async def check_coverage(page, task):
    row = task_row(page, task["id"])
    await expand(row)
    items = task["data_items"]
    await expect(row.locator(".task-data-table tbody tr")).to_have_count(len(items))
    if items:
        await expect(row.locator(".task-data-table thead")).to_contain_text("核验 / 完整截至")
    for index, item in enumerate(items):
        cells = row.locator(".task-data-table tbody tr").nth(index).locator("td")
        await expect(cells.nth(0)).to_have_text(item["name"])
        await expect(cells.nth(1)).to_have_text(item["date"] or "未记录")
        await expect(cells.nth(2)).to_have_text(item["complete_date"] or "未记录")
        if item["status"] in DATA_LABELS:
            await expect(cells.nth(3)).to_contain_text(DATA_LABELS[item["status"]])
        if item["note"]:
            await expect(cells.nth(3).locator("small")).to_be_hidden()
            await cells.nth(3).locator(".task-data-note summary").click()
            await expect(cells.nth(3).locator("small")).to_be_visible()
            assert await cells.nth(3).locator("small").text_content() == item["note"]
            await cells.nth(3).locator(".task-data-note summary").click()
    return len(items)


async def refresh(page):
    async with page.expect_response(lambda response: response.url == API) as event:
        await page.locator("#tasks-refresh").click()
    response = await event.value
    await expect(page.locator("#tasks-refresh")).to_be_enabled()
    return response


async def no_overflow(page):
    assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")


async def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(API)
        response.raise_for_status()
        validate_payload(response.json())

    async with async_playwright() as playwright:
        browser = await playwright.webkit.launch()
        page = await browser.new_page(viewport={"width": 1440, "height": 1000})
        failures = []
        requests = []
        page.on("pageerror", lambda error: failures.append(str(error)))
        page.on("request", lambda request: requests.append((request.method, request.url)))
        try:
            async with page.expect_response(lambda response: response.url == API) as event:
                await page.goto(f"{URL}/?index=csi300")
            snapshot = await (await event.value).json()
            await check_rows(page, snapshot)
            await expect(page.locator("#loading")).to_be_hidden()
            await expect(page.locator("#error")).to_be_hidden()
            assert await page.locator("#chart path").count() > 0
            chart_price = await page.locator("#price").text_content()
            await page.locator(".tasks-nav").click()
            assert page.url.endswith("#scheduled-tasks")
            await page.locator("#scheduled-tasks").screenshot(
                path=OUTPUT / "scheduled-tasks-desktop.png"
            )

            coverage = {}
            for task in snapshot["tasks"]:
                if task["id"] in {"erp", "index_us", "index_asia"}:
                    coverage[task["id"]] = await check_coverage(page, task)
            original_results = 0
            for task in snapshot["tasks"]:
                original = task["last_run"]["result_text"]
                if original:
                    row = task_row(page, task["id"])
                    await expand(row)
                    await row.locator(".task-report summary").click()
                    assert await row.locator(".task-result-text").text_content() == original
                    original_results += 1

            open_ids = await page.locator(".task-row[open]").evaluate_all(
                "rows => rows.map(row => row.dataset.taskId)"
            )
            response = await refresh(page)
            assert response.status == 200
            snapshot = await response.json()
            await check_rows(page, snapshot)
            for task_id in open_ids:
                await expect(task_row(page, task_id)).to_have_attribute("open", "")

            await page.set_viewport_size({"width": 375, "height": 844})
            for task_id in TASK_IDS:
                await expand(task_row(page, task_id), False)
            await no_overflow(page)
            await expect(page.locator(".tasks-nav")).to_be_visible()
            await page.locator("#scheduled-tasks").screenshot(
                path=OUTPUT / "scheduled-tasks-mobile.png"
            )
            for task_id in TASK_IDS:
                await expand(task_row(page, task_id))
            await no_overflow(page)
            await page.locator("#scheduled-tasks").screenshot(
                path=OUTPUT / "scheduled-tasks-mobile-expanded.png"
            )

            previous_rows = await page.locator("#tasks-list").text_content()

            async def unavailable(route):
                await route.fulfill(status=503, body="Unavailable")

            await page.route(API, unavailable)
            response = await refresh(page)
            assert response.status == 503
            await expect(page.locator("#tasks-error")).to_contain_text("保留上次")
            await expect(page.locator("#tasks-refresh")).to_have_text("重试读取")
            assert await page.locator("#tasks-list").text_content() == previous_rows
            await expect(page.locator("#error")).to_be_hidden()
            await expect(page.locator("#price")).to_have_text(chart_price)
            assert await page.locator("#chart path").count() > 0
            await page.unroute(API, unavailable)
            response = await refresh(page)
            assert response.status == 200
            snapshot = await response.json()
            await expect(page.locator("#tasks-error")).to_be_hidden()
            await check_rows(page, snapshot)

            injected = copy.deepcopy(snapshot)
            malicious = '<img src=x onerror="window.__taskInjection=true"> & <script>x</script>'
            injected["tasks"][0]["name"] = malicious
            injected["tasks"][0]["last_run"]["summary"] = malicious
            injected["tasks"][0]["last_run"]["result_text"] = malicious + "\n原文保留换行"
            injected["tasks"][0]["last_run"]["issues"] = [malicious]

            async def inject_text(route):
                await route.fulfill(json=injected)

            await page.route(API, inject_text)
            await refresh(page)
            row = task_row(page, injected["tasks"][0]["id"])
            await expect(row.locator(".task-name")).to_have_text(malicious)
            await expand(row)
            if not await row.locator(".task-report").evaluate("element => element.open"):
                await row.locator(".task-report summary").click()
            assert await row.locator(".task-result-text").text_content() == (
                malicious + "\n原文保留换行"
            )
            assert await page.locator("#scheduled-tasks img, #scheduled-tasks script").count() == 0
            assert await page.evaluate("window.__taskInjection") is None
            await no_overflow(page)
            await page.unroute(API, inject_text)
            await refresh(page)

            assert not failures, failures
            assert all(method == "GET" for method, _ in requests), requests
            report = {
                "browser": "WebKit",
                "status": "passed",
                "checked_at": snapshot["checked_at"],
                "tasks": [
                    {
                        "id": task["id"],
                        "name": task["name"],
                        "schedule": task["schedule_text"],
                        "scheduler_state": task["scheduler"]["state"],
                        "run_status": task["last_run"]["status"],
                    }
                    for task in snapshot["tasks"]
                ],
                "coverage_rows": coverage,
                "original_results_checked": original_results,
                "scheduled_api_gets": sum(url == API for _, url in requests),
                "checks": [
                    "five_live_tasks_match_api_names_schedules_and_separate_statuses",
                    "erp_and_index_data_dates_and_complete_dates_match_api",
                    "data_notes_expand_to_show_full_text_without_oversized_default_rows",
                    "original_result_text_preserved",
                    "manual_refresh_get_only_and_open_state_preserved",
                    "mobile_375_collapsed_and_expanded_no_body_overflow",
                    "503_retains_previous_tasks_without_breaking_chart",
                    "retry_recovers_live_status",
                    "server_markup_rendered_as_inert_text",
                    "no_javascript_errors_or_mutating_requests",
                ],
            }
            (OUTPUT / "scheduled-tasks-web-validation.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(json.dumps(report, ensure_ascii=False, indent=2))
        except Exception:
            await page.screenshot(path=OUTPUT / "scheduled-tasks-web-error.png", full_page=True)
            print("Browser errors:", failures)
            raise
        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
