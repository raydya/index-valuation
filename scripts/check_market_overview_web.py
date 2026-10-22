"""Check latest market signals independently from historical charts using WebKit."""

import asyncio
import copy
import json
import os
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import httpx
from playwright.async_api import async_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data/exports"
URL = os.environ.get("WEB_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
API = f"{URL}/api/market-overview"
ACTIONS = {
    "panic": "恐慌，持续买入",
    "normal": "普通，继续持有",
    "greed": "贪婪，减仓离场",
    "unknown": "暂无判断",
}
COLORS = {
    "panic": "rgb(24, 135, 91)",
    "normal": "rgb(176, 123, 18)",
    "greed": "rgb(196, 67, 54)",
    "unknown": "rgb(127, 139, 158)",
}


async def check_items(page, snapshot):
    await expect(page.locator(".market-index-link")).to_have_count(len(snapshot["items"]))
    for item in snapshot["items"]:
        row = page.locator(f'[data-market-index="{item["key"]}"]')
        await expect(row.locator(".market-index-name")).to_have_text(item["name"])
        await expect(row).to_have_attribute("data-signal", item["signal"])
        await expect(row.locator(".market-action")).to_have_text(ACTIONS[item["signal"]])
        await expect(row.locator(".market-index-date")).to_have_text(item["as_of"] or "日期未记录")
        expected_pe = (
            "未记录"
            if item["pe"] is None
            else f"{Decimal(item['pe']).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):,.2f}×"
        )
        await expect(row.locator(".market-index-pe")).to_have_text(f"PE {expected_pe}")
        color = await row.locator(".market-dot").evaluate(
            "node => getComputedStyle(node).backgroundColor"
        )
        assert color == COLORS[item["signal"]]
        if item["signal"] == "unknown":
            await expect(row.locator(".market-reason")).to_have_text(item["reason"])
        else:
            await expect(row.locator(".market-reason")).to_have_count(0)
    market_codes = {item["market"] for item in snapshot["items"]}
    await expect(page.locator(".market-group")).to_have_count(len(market_codes))


async def refresh(page):
    async with page.expect_response(lambda response: response.url == API) as event:
        await page.locator("#market-overview-refresh").click()
    response = await event.value
    await expect(page.locator("#market-overview-refresh")).to_be_enabled()
    return response


async def no_overflow(page):
    assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")


async def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(API)
        response.raise_for_status()
        snapshot = response.json()
        indices_response = await client.get(f"{URL}/api/indices")
        indices_response.raise_for_status()
        expected_keys = {index["key"] for index in indices_response.json()}
        assert {item["key"] for item in snapshot["items"]} == expected_keys

    async with async_playwright() as playwright:
        browser = await playwright.webkit.launch()
        page = await browser.new_page(viewport={"width": 1440, "height": 1100})
        errors = []
        requests = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("request", lambda request: requests.append((request.method, request.url)))
        try:
            async with page.expect_response(lambda response: response.url == API) as event:
                await page.goto(f"{URL}/?index=csi300")
            snapshot = await (await event.value).json()
            await check_items(page, snapshot)
            await expect(page.locator("#loading")).to_be_hidden()
            await expect(page.locator("#error")).to_be_hidden()
            await expect(page.locator('[data-market-index="csi300"]')).to_have_attribute(
                "aria-current", "true"
            )
            await page.locator("#market-overview").screenshot(
                path=OUTPUT / "market-overview-desktop.png"
            )
            await page.screenshot(path=OUTPUT / "market-overview-desktop-page.png")

            async with page.expect_response(
                lambda response: "/api/indices/sp500/dashboard?" in response.url
            ):
                await page.locator('[data-market-index="sp500"]').click()
            await expect(page.locator("#index-select")).to_have_value("sp500")
            await expect(page.locator('[data-market-index="sp500"]')).to_have_attribute(
                "aria-current", "true"
            )

            async with page.expect_response(
                lambda response: "/api/indices/csi300/dashboard?" in response.url
            ) as event:
                await page.locator('[data-market-index="csi300"]').focus()
                await page.keyboard.press("Enter")
            dashboard = await (await event.value).json()
            await expect(page.locator("#chart-wrap")).to_have_attribute("aria-busy", "false")
            await expect(page.locator("#index-select")).to_have_value("csi300")
            historical_date = dashboard["series"][-100]["trade_date"]
            current_overview = await page.locator("#market-overview-groups").text_content()
            async with page.expect_response(
                lambda response: (
                    "/api/indices/csi300/dashboard?" in response.url
                    and f"as_of={historical_date}" in response.url
                )
            ):
                await page.locator("#as-of").fill(historical_date)
                await page.locator("#as-of").dispatch_event("change")
            await expect(page.locator("#panic-live-stamp")).to_contain_text(historical_date)
            assert await page.locator("#market-overview-groups").text_content() == current_overview
            await check_items(page, snapshot)

            await page.set_viewport_size({"width": 375, "height": 844})
            await no_overflow(page)
            await page.locator("#market-overview").screenshot(
                path=OUTPUT / "market-overview-mobile.png"
            )
            await page.locator(".market-overview-rule summary").click()
            await no_overflow(page)
            await expect(page.locator(".market-overview-rule")).to_contain_text(
                "红灯：贪婪，减仓离场"
            )
            await page.locator(".market-overview-rule summary").click()

            async def unavailable(route):
                await route.fulfill(status=503, body="Unavailable")

            await page.route(API, unavailable)
            previous = await page.locator("#market-overview-groups").text_content()
            response = await refresh(page)
            assert response.status == 503
            await expect(page.locator("#market-overview-error")).to_contain_text("可能已过期")
            assert await page.locator("#market-overview-groups").text_content() == previous
            await expect(page.locator("#error")).to_be_hidden()
            assert await page.locator("#chart path").count() > 0
            await expect(page.locator(".task-row")).to_have_count(6)
            await page.unroute(API, unavailable)
            response = await refresh(page)
            assert response.status == 200
            snapshot = await response.json()
            await expect(page.locator("#market-overview-error")).to_be_hidden()
            await check_items(page, snapshot)

            mocked = copy.deepcopy(snapshot)
            malicious = '<img src=x onerror="window.__marketInjection=true">'
            for item, signal in zip(mocked["items"][:4], ACTIONS, strict=True):
                item["signal"] = signal
                item["reason"] = malicious if signal == "unknown" else "模拟状态"
                if signal == "unknown":
                    item["pe"] = None
            mocked["items"][0]["name"] = malicious
            mocked["rule"]["description"] = malicious
            calls = 0

            async def mocked_states(route):
                nonlocal calls
                calls += 1
                await asyncio.sleep(0.15)
                await route.fulfill(json=mocked)

            await page.route(API, mocked_states)
            async with page.expect_response(lambda response: response.url == API):
                await page.locator("#market-overview-refresh").click()
                await expect(page.locator("#market-overview-refresh")).to_be_disabled()
                await page.locator("#market-overview-refresh").dispatch_event("click")
            await expect(page.locator("#market-overview-refresh")).to_be_enabled()
            assert calls == 1, "Concurrent refresh must not issue a second request"
            await check_items(page, mocked)
            assert await page.locator("#market-overview img, #market-overview script").count() == 0
            assert await page.evaluate("window.__marketInjection") is None
            await expect(page.locator("#market-overview-rule-text")).to_have_text(malicious)
            await no_overflow(page)
            await page.unroute(API, mocked_states)
            await refresh(page)

            assert not errors, errors
            assert all(method == "GET" for method, _ in requests), requests
            report = {
                "browser": "WebKit",
                "status": "passed",
                "checked_at": snapshot["checked_at"],
                "items": [
                    {
                        "key": item["key"],
                        "signal": item["signal"],
                        "as_of": item["as_of"],
                        "pe": item["pe"],
                        "reason": item["reason"] if item["signal"] == "unknown" else None,
                    }
                    for item in snapshot["items"]
                ],
                "historical_chart_date": historical_date,
                "checks": [
                    "all_configured_indices_grouped_by_market_match_live_api",
                    "signal_text_and_actual_data_date_pe_and_color_match_backend",
                    "unknown_is_gray_and_reason_is_visible",
                    "mouse_and_keyboard_select_index_without_reloading_overview",
                    "latest_overview_unchanged_by_historical_chart_date",
                    "mobile_375_no_body_overflow_with_rules_open_or_closed",
                    "503_retains_last_snapshot_and_chart_and_tasks_keep_working",
                    "retry_recovers_actual_overview",
                    "mock_green_yellow_red_gray_actions_and_colors",
                    "inert_server_markup_in_name_reason_and_rule",
                    "refresh_overlap_guard_and_get_only_requests",
                    "no_javascript_errors",
                ],
            }
            (OUTPUT / "market-overview-web-validation.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(json.dumps(report, ensure_ascii=False, indent=2))
        except Exception:
            await page.screenshot(path=OUTPUT / "market-overview-web-error.png", full_page=True)
            print("Browser errors:", errors)
            raise
        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
