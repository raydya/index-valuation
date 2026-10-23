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
    "panic": "恐慌",
    "normal": "普通",
    "greed": "贪婪",
    "unknown": "暂无判断",
}
COLORS = {
    "panic": "rgb(24, 135, 91)",
    "normal": "rgb(176, 123, 18)",
    "greed": "rgb(196, 67, 54)",
    "unknown": "rgb(127, 139, 158)",
}
TEXT_COLORS = {
    "panic": {"rgb(24, 135, 91)"},
    "normal": {"rgb(81, 97, 118)", "rgb(148, 102, 13)"},
    "greed": {"rgb(182, 58, 46)"},
}
HEADERS = ["指数", "PE", "历史分位", "估值状态"]


def number(value, places):
    if value is None:
        return "暂无"
    rounded = Decimal(str(value)).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    return f"{rounded:,.{places}f}"


async def check_items(page, snapshot):
    await expect(page.locator(".market-index-link")).to_have_count(len(snapshot["items"]))
    await expect(page.locator("tr.market-item")).to_have_count(len(snapshot["items"]))
    dates = {item["as_of"] or "unknown" for item in snapshot["items"]}
    await expect(page.locator("section.market-group[data-date]")).to_have_count(len(dates))
    for day in dates:
        group = page.locator(f'section.market-group[data-date="{day}"]')
        await expect(group.locator("h3.market-group-title")).to_have_text(
            f"指数估值：{day if day != 'unknown' else '暂无'}"
        )
        await expect(group.locator("table.market-table")).to_have_count(1)
        await expect(group.locator("thead th")).to_have_text(HEADERS)
        await expect(group.locator("tr.market-item")).to_have_count(
            sum((item["as_of"] or "unknown") == day for item in snapshot["items"])
        )
    await expect(page.locator(".market-item .market-index-date")).to_have_count(0)
    for item in snapshot["items"]:
        group = page.locator(f'.market-group[data-date="{item["as_of"] or "unknown"}"]')
        row = group.locator(f'tr.market-item[data-index-key="{item["key"]}"]')
        await expect(row.locator(":scope > th, :scope > td")).to_have_count(4)
        await expect(row.locator(':scope > th[scope="row"]')).to_have_count(1)
        link = row.locator(
            f'th[scope="row"] a.market-index-link[data-market-index="{item["key"]}"]'
        )
        await expect(link.locator(".market-index-name")).to_have_text(item["name"])
        await expect(row).to_have_attribute("data-signal", item["signal"])
        await expect(row).to_have_attribute("data-market", item["market"])
        judgement = row.locator("td .market-judgement")
        await expect(judgement.locator(".market-action")).to_have_text(ACTIONS[item["signal"]])
        pe = row.locator(".market-index-pe")
        await expect(pe).to_have_text(number(item["pe"], 2))
        percentile = row.locator(".market-index-percentile")
        await expect(percentile).to_have_text(
            "暂无" if item["percentile"] is None else number(item["percentile"], 1) + "%"
        )
        assert await pe.evaluate("node => Boolean(node.closest('td'))")
        assert await percentile.evaluate("node => Boolean(node.closest('td'))")
        if item["as_of"]:
            assert item["as_of"] not in await row.inner_text()
        color = await judgement.locator(".market-dot").evaluate(
            "node => getComputedStyle(node).backgroundColor"
        )
        assert color == COLORS[item["signal"]]
        if item["signal"] in TEXT_COLORS:
            rank_color = await percentile.evaluate("node => getComputedStyle(node).color")
            state_color = await judgement.evaluate("node => getComputedStyle(node).color")
            assert rank_color in TEXT_COLORS[item["signal"]], (item["key"], rank_color)
            assert state_color in TEXT_COLORS[item["signal"]], (item["key"], state_color)
            if item["signal"] in {"panic", "greed"}:
                assert rank_color == state_color
        await expect(row.locator(".market-reason")).to_have_count(0)
        reason = page.locator(f'li.market-reason[data-reason-index="{item["key"]}"]')
        if item["signal"] == "unknown":
            await expect(reason).to_have_count(1)
            await expect(reason).to_contain_text(item["name"])
            await expect(reason).to_contain_text(item["reason"])
            reason_id = await reason.get_attribute("id")
            assert reason_id, "Unknown-state explanation needs an accessible identity"
            assert reason_id in (await judgement.get_attribute("aria-describedby") or "").split()
        else:
            await expect(reason).to_have_count(0)
    unknowns = [item for item in snapshot["items"] if item["signal"] == "unknown"]
    notes = page.locator("details.market-overview-notes")
    await expect(notes).to_have_count(1 if unknowns else 0)
    if unknowns:
        await expect(notes.locator("li.market-reason")).to_have_count(len(unknowns))
        was_open = await notes.get_attribute("open") is not None
        if not was_open:
            await notes.locator("summary").click()
        for item in unknowns:
            await expect(notes.locator(f'[data-reason-index="{item["key"]}"]')).to_be_visible()
        if not was_open:
            await notes.locator("summary").click()


async def refresh(page):
    async with page.expect_response(lambda response: response.url == API) as event:
        await page.locator("#market-overview-refresh").click()
    response = await event.value
    await expect(page.locator("#market-overview-refresh")).to_be_enabled()
    return response


async def no_overflow(page):
    assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
    assert await page.locator("table.market-table").evaluate_all(
        "nodes => nodes.every(node => { const rect = node.getBoundingClientRect(); "
        "return rect.left >= -1 && rect.right <= innerWidth + 1 "
        "&& node.scrollWidth <= node.clientWidth + 1; })"
    ), "Market tables must fit the viewport without clipped or horizontally scrolling content"


async def check_mobile(page, *, screenshots=False):
    for width in (375, 320):
        await page.set_viewport_size({"width": width, "height": 844})
        await no_overflow(page)
        if screenshots:
            name = (
                "market-overview-mobile.png" if width == 375 else "market-overview-mobile-320.png"
            )
            await page.locator("#market-overview").screenshot(path=OUTPUT / name)
        await page.locator(".market-overview-rule summary").click()
        await no_overflow(page)
        await page.locator(".market-overview-rule summary").click()
        notes = page.locator("details.market-overview-notes")
        if await notes.count():
            await notes.locator("summary").click()
            await no_overflow(page)
            await notes.locator("summary").click()


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
            await no_overflow(page)
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

            await check_mobile(page, screenshots=True)

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
            for index, (item, signal) in enumerate(zip(mocked["items"][:4], ACTIONS, strict=True)):
                item["signal"] = signal
                item["reason"] = malicious if signal == "unknown" else "模拟状态"
                item["as_of"] = "2026-10-22" if index < 2 else "2026-10-21"
                item["percentile"] = ["2.34", "63.15", "97.56", None][index]
                if signal == "unknown":
                    item["pe"] = None
                    item["as_of"] = None
                else:
                    item["pe"] = ["8.11", "12.74", "30.09"][index]
            # Two different markets sharing a date belong to the same table.
            mocked["items"][0]["market"] = "CN"
            mocked["items"][1]["market"] = "US"
            mocked["items"][2]["market"] = "HK"
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
            await check_mobile(page)
            notes = page.locator("details.market-overview-notes")
            if await notes.count():
                await notes.locator("summary").click()
                await notes.locator("summary").focus()
                mocked["items"][0]["pe"] = "8.12"
                async with page.expect_response(lambda response: response.url == API):
                    # Keep focus on the explanation while triggering an automatic-style refresh.
                    await page.locator("#market-overview-refresh").dispatch_event("click")
                await expect(page.locator("#market-overview-refresh")).to_be_enabled()
                await expect(
                    page.locator(
                        f'tr.market-item[data-index-key="{mocked["items"][0]["key"]}"] '
                        ".market-index-pe"
                    )
                ).to_have_text("8.12")
                await expect(notes).to_have_attribute("open", "")
                await expect(notes.locator("summary")).to_be_focused()
                await notes.locator("summary").click()
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
                        "percentile": item["percentile"],
                        "reason": item["reason"] if item["signal"] == "unknown" else None,
                    }
                    for item in snapshot["items"]
                ],
                "historical_chart_date": historical_date,
                "checks": [
                    "all_configured_indices_grouped_by_actual_date_match_live_api",
                    "four_column_table_headers_and_row_headers_are_semantic",
                    "signal_text_pe_percentile_and_color_match_backend",
                    "dates_appear_in_group_headers_instead_of_repeating_in_rows",
                    "unknown_is_gray_and_reason_is_accessible_in_shared_details",
                    "mouse_and_keyboard_select_index_without_reloading_overview",
                    "latest_overview_unchanged_by_historical_chart_date",
                    "mobile_320_and_375_no_body_or_table_overflow_with_details_open_or_closed",
                    "503_retains_last_snapshot_and_chart_and_tasks_keep_working",
                    "retry_recovers_actual_overview",
                    "mock_green_yellow_red_gray_labels_and_percentile_colors",
                    "mock_same_date_across_markets_and_unknown_date_grouping",
                    "changed_snapshot_preserves_open_notes_and_summary_focus",
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
