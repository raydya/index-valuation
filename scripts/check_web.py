"""Exercise the live dashboard in WebKit; leaves screenshots and a validation report."""

import asyncio
import csv
import json
import math
from pathlib import Path

import httpx
from playwright.async_api import async_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data/exports"
URL = "http://127.0.0.1:8000"


def display(value, digits=2):
    return "—" if value is None else f"{float(value):,.{digits}f}"


async def check_price_return(page, data):
    summary = data["price_return"]
    value = summary["annualized_return_pct"]
    expected = "—" if value is None else f"{'+' if float(value) > 0 else ''}{display(value)}%"
    await expect(page.locator("#annualized-return")).to_have_text(expected)
    window = f"近{data['chart_years']}年" if data["chart_years"] else "全部历史"
    await expect(page.locator("#return-basis")).to_have_text(f"{window} · 不含分红")
    if summary["start_date"]:
        await expect(page.locator("#return-range")).to_have_text(
            f"{summary['start_date']} 至 {summary['end_date']}"
        )
    if summary["price_status"] == "unverified":
        await expect(page.locator("#return-status")).to_contain_text("未核验价格")


async def check_panic_line(page, data, mode="fixed"):
    research = data["panic_reference"]
    value = None
    if research["comparison"]["status"] == "available":
        value = (
            research["record"]["threshold_pe"]
            if mode == "pe"
            else research["comparison"]["reference_points"]
        )
    line = page.locator("#panic-line")
    if value is None:
        await expect(line).to_have_count(0)
        return
    # A horizontal SVG line has a zero-height geometric bounding box even
    # though its stroke is painted; verify attachment, paint and its label.
    await expect(line).to_have_count(1)
    await expect(page.locator("#panic-line-label")).to_be_visible()
    assert math.isclose(float(await line.get_attribute("data-value")), float(value), rel_tol=1e-12)
    geometry = await line.evaluate("""line => {
        const box = line.ownerSVGElement.viewBox.baseVal;
        const label = document.getElementById('panic-line-label').getBBox();
        const style = getComputedStyle(line);
        return {x1: line.x1.baseVal.value, x2: line.x2.baseVal.value,
            y1: line.y1.baseVal.value, y2: line.y2.baseVal.value,
            width: box.width, height: box.height, labelLeft: label.x,
            labelRight: label.x + label.width, stroke: style.stroke,
            strokeWidth: parseFloat(style.strokeWidth), display: style.display,
            visibility: style.visibility, opacity: parseFloat(style.opacity)};
    }""")
    assert geometry["y1"] == geometry["y2"], "Panic reference must be horizontal"
    assert geometry["stroke"] != "none" and geometry["strokeWidth"] > 0
    assert geometry["display"] != "none" and geometry["visibility"] == "visible"
    assert geometry["opacity"] > 0
    assert 24 <= geometry["y1"] <= geometry["height"] - 43
    assert geometry["x1"] > 0 and geometry["x2"] > geometry["width"] * 0.9
    assert geometry["labelLeft"] >= 0 and geometry["labelRight"] <= geometry["width"]
    await expect(page.locator("#panic-line-label")).to_contain_text(
        f"{display(research['record']['threshold_pe'])}×"
    )
    if mode != "pe":
        await expect(page.locator("#panic-line-label")).to_contain_text(f"{display(value)}点")
        await expect(page.locator("#chart-caption")).to_contain_text(data["selected"]["trade_date"])
    await expect(page.locator("#legend")).to_contain_text("恐慌估值线")


async def check_panic_reference(page, data):
    research = data["panic_reference"]
    record, comparison = research["record"], research["comparison"]
    await expect(page.locator("#panic-section")).to_be_visible()
    await expect(page.locator("#panic-stamp")).to_contain_text(research["study_date"])
    threshold = record["threshold_pe"] if record else None
    await expect(page.locator("#panic-threshold")).to_have_text(
        "暂不设定" if threshold is None else f"≤ {display(threshold)}×"
    )
    gap = comparison["gap_pct"]
    gap_text = "—"
    if gap is not None:
        gap = float(gap)
        gap_text = (
            "等于观察线"
            if gap == 0
            else (f"{'高于' if gap > 0 else '低于'} {display(abs(gap), 1)}%")
        )
    await expect(page.locator("#panic-gap")).to_have_text(gap_text)
    await expect(page.locator("#panic-selected")).to_contain_text(comparison["date"])
    await check_panic_line(page, data)
    if record and not record["three_year"]["n"]:
        await expect(page.locator("#panic-return")).to_have_text("—")
        await expect(page.locator("#panic-sample-count")).to_have_text("尚无成熟3年样本")
    await page.locator("#panic-evidence summary").click()
    samples = record["three_year"]["nonoverlap_samples"] if record else []
    await expect(page.locator("#panic-samples tr")).to_have_count(len(samples) or 1)
    if samples:
        await expect(page.locator("#panic-samples tr").first).to_contain_text(
            samples[0]["signal_date"]
        )
    await page.locator("#panic-overview summary").click()
    await expect(page.locator("#panic-overview-rows tr")).to_have_count(len(research["overview"]))
    await expect(page.locator("#panic-overview-rows .panic-current")).to_contain_text(
        data["index"]["name"]
    )
    await page.locator("#panic-section").screenshot(
        path=OUTPUT / f"{data['index']['key']}-panic-reference.png"
    )
    await page.set_viewport_size({"width": 390, "height": 844})
    await page.wait_for_function("document.getElementById('chart').viewBox.baseVal.width < 500")
    await check_panic_line(page, data)
    assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
    await page.locator(".workspace").screenshot(
        path=OUTPUT / f"{data['index']['key']}-panic-line-mobile.png"
    )
    await page.locator("#panic-section").screenshot(
        path=OUTPUT / f"{data['index']['key']}-panic-mobile.png"
    )
    await page.set_viewport_size({"width": 1440, "height": 1050})
    await page.locator("#panic-evidence summary").click()
    await page.locator("#panic-overview summary").click()


async def check_additional_indices(page, dashboards):
    """Every configured index shares selection, calculation and export behavior."""
    results = []
    for data in dashboards:
        index, selected = data["index"], data["selected"]
        key = index["key"]
        await page.set_viewport_size({"width": 1440, "height": 1050})
        await page.locator("#index-select").select_option(key)
        await expect(page.locator("#index-title")).to_have_text(index["name"])
        assert f"index={key}" in page.url
        await page.reload(wait_until="networkidle")
        await expect(page.locator("#index-select")).to_have_value(key)
        await expect(page.locator("#index-title")).to_have_text(index["name"])
        await expect(page.locator("#code")).to_have_text(index["code"])
        await expect(page.locator("#price")).to_have_text(display(selected["price"]))
        await expect(page.locator("#pe")).to_have_text(display(selected["pe"], 3))
        await expect(page.locator("#eps")).to_have_text(display(selected["implied_eps"]))
        await check_price_return(page, data)
        await check_panic_reference(page, data)
        if index.get("tracking_etfs"):
            await expect(page.locator("#benchmark-note")).to_contain_text(index["tracking_etfs"][0])
        if index["valuation"]["source"] == "none":
            await expect(page.locator("#quality-note")).to_contain_text("富途 OpenD 不支持")
        if selected.get("pe_usable") is False:
            await expect(page.locator("#quality-note")).to_contain_text("重大来源差异")
            if data["latest_complete_date"]:
                await page.locator("#last-usable").click()
                await expect(page.locator("#as-of")).to_have_value(data["latest_complete_date"])
                await expect(page.locator("#eps")).not_to_have_text("—")
                await page.locator("#latest").click()
                await expect(page.locator("#as-of")).to_have_value(data["available_end"])
                await expect(page.locator("#eps")).to_have_text("—")
        if index["valuation"]["source"] == "csindex":
            await expect(page.locator("#quality-note")).to_contain_text("以中证指数官方数据为准")
            if selected.get("price_usable") and selected["price_status"] == "unverified":
                await expect(page.locator("#quality-note")).to_contain_text("采用官方点位")
        await expect(page.locator(".mean-row")).to_contain_text(display(selected["mean_points"]))
        await expect(page.locator("#band-rows tr")).to_have_count(len(index["band_multiples"]) + 1)
        if selected["history_span_short"]:
            await expect(page.locator("#band-footnote")).to_contain_text("历史不足5年")
        await page.locator("[data-mode=quantile]").click()
        await expect(page.locator("#band-rows tr")).to_have_count(6)
        await check_panic_line(page, data, "quantile")
        await page.locator("[data-mode=pe]").click()
        await expect(page.locator("#chart-title")).to_contain_text("PE 历史走势")
        await check_panic_line(page, data, "pe")
        await page.locator("[data-mode=fixed]").click()
        missing = next((r for r in data["series"] if r["pe"] is None), None)
        if missing:
            await page.locator("#as-of").fill(missing["trade_date"])
            await page.locator("#as-of").dispatch_event("change")
            await expect(page.locator("#price-source")).to_contain_text(missing["trade_date"])
            await expect(page.locator("#pe")).to_have_text("—")
            await expect(page.locator("#eps")).to_have_text("—")
            await expect(page.locator("#panic-line")).to_have_count(0)
            await page.locator("#latest").click()
            await expect(page.locator("#as-of")).to_have_value(data["available_end"])
            await expect(page.locator("#pe")).to_have_text(display(selected["pe"], 3))
        async with page.expect_download() as event:
            await page.locator("#export").click()
        download = await event.value
        assert download.suggested_filename.startswith(f"{key}-bands-")
        path = OUTPUT / f"{key}-web-download.csv"
        await download.save_as(path)
        rows = list(csv.DictReader(path.open(encoding="utf-8-sig")))
        assert rows[-1]["price"] == selected["price"]
        assert rows[-1]["pe"] == (selected["pe"] or "")
        assert rows[-1]["mean_pe"] == (selected["mean_pe"] or "")
        assert rows[-1]["trade_date"] == data["available_end"]
        summary = data["price_return"]
        exported = {row["trade_date"]: row for row in rows}
        for endpoint in ("start", "end"):
            day = summary[f"{endpoint}_date"]
            if day:
                assert exported[day]["price"] == summary[f"{endpoint}_price"]
        await page.screenshot(path=OUTPUT / f"{key}-web-desktop.png", full_page=True)
        await page.set_viewport_size({"width": 390, "height": 844})
        await page.wait_for_function("document.getElementById('chart').viewBox.baseVal.width < 500")
        assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        await page.screenshot(path=OUTPUT / f"{key}-web-mobile.png", full_page=True)
        results.append({"index": key, "latest_date": data["available_end"], "csv_rows": len(rows)})
    return results


async def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(base_url=URL, timeout=30) as client:
        response = await client.get("/api/indices/csi300/dashboard")
        response.raise_for_status()
        data = response.json()
        response = await client.get("/api/indices/csi300/dashboard?history_years=10")
        response.raise_for_status()
        ten_year = response.json()
        response = await client.get("/api/indices/csi300/dashboard?chart_years=1")
        response.raise_for_status()
        one_year = response.json()
        response = await client.get("/api/indices")
        response.raise_for_status()
        extra = []
        for index in response.json():
            if index["key"] != data["index"]["key"]:
                response = await client.get(f"/api/indices/{index['key']}/dashboard")
                response.raise_for_status()
                extra.append(response.json())
    failures = []
    async with async_playwright() as playwright:
        browser = await playwright.webkit.launch()
        context = await browser.new_context(
            viewport={"width": 1440, "height": 1050}, device_scale_factor=1
        )
        page = await context.new_page()
        page.on("pageerror", lambda error: failures.append(str(error)))
        page.on(
            "console",
            lambda message: failures.append(message.text) if message.type == "error" else None,
        )
        try:
            await page.goto(URL, wait_until="networkidle")
            await expect(page.locator("#pe")).to_have_text(f"{float(data['selected']['pe']):,.3f}")
            await check_price_return(page, data)
            await check_panic_reference(page, data)
            async with page.expect_download() as event:
                await page.locator("#panic-download").click()
            research_download = await event.value
            research_path = OUTPUT / "panic-reference-web-download.json"
            await research_download.save_as(research_path)
            downloaded = json.loads(research_path.read_text())
            assert downloaded["indices"]["csi300"] == data["panic_reference"]["record"]
            await expect(page.locator("#band-rows tr")).to_have_count(
                len(data["selected"]["fixed"]) + 1
            )
            await expect(page.locator("#mean-summary")).to_have_text(
                f"近5年平均 {float(data['selected']['mean_pe']):,.2f}×"
            )
            await expect(page.locator(".mean-row")).to_contain_text(
                f"{float(data['selected']['mean_points']):,.2f}"
            )
            await expect(page.locator("#mean-line")).to_have_attribute("stroke-dasharray", "6 4")
            assert await page.locator("#mean-line").get_attribute("d")
            assert await page.locator("#chart path").count() >= 7
            await page.locator("#chart").focus()
            await page.keyboard.press("ArrowLeft")
            await expect(page.locator("#tooltip")).to_be_visible()
            await expect(page.locator("#tooltip")).to_contain_text("近5年平均 PE")
            await page.locator("[data-mode=quantile]").click()
            await expect(page.locator("#band-rows tr")).to_have_count(6)
            await check_panic_line(page, data, "quantile")
            await page.locator("[data-mode=pe]").click()
            await expect(page.locator("#chart-title")).to_have_text("PE 历史走势")
            await check_panic_line(page, data, "pe")
            await page.locator('[data-years="1"]').click()
            await expect(page.locator('[data-years="1"]')).to_have_attribute("aria-pressed", "true")
            await check_price_return(page, one_year)
            await check_panic_line(page, one_year, "pe")
            await expect(page.locator("#panic-threshold")).to_have_text(
                f"≤ {display(data['panic_reference']['record']['threshold_pe'])}×"
            )
            await page.locator("#history-window").select_option("10")
            await expect(page.locator("#percentile-label")).to_have_text("近10年 PE 分位")
            await expect(page.locator("#mean-summary")).to_have_text(
                f"近10年平均 {float(ten_year['selected']['mean_pe']):,.2f}×"
            )
            await check_price_return(page, one_year)
            await page.locator('[data-years="0"]').click()
            await expect(page.locator('[data-years="0"]')).to_have_attribute("aria-pressed", "true")
            await expect(page.locator("#table-description")).to_contain_text(
                f"{data['available_sessions']:,}"
            )
            await page.locator("#as-of").fill(data["available_start"])
            await page.locator("#as-of").dispatch_event("change")
            await expect(page.locator("#annualized-return")).to_have_text("—")
            await expect(page.locator("#return-status")).to_contain_text("至少需要两个")
            await page.locator("#as-of").fill("2004-12-31")
            await page.locator("#as-of").dispatch_event("change")
            await expect(page.locator("#pe")).to_have_text("—")
            await expect(page.locator("#eps")).to_have_text("—")
            await expect(page.locator("#mean-summary")).to_have_text("近10年平均 —")
            await expect(page.locator("#return-range")).to_contain_text("至 2004-12-31")
            await page.locator("#as-of").fill("2005-01-04")
            await page.locator("#as-of").dispatch_event("change")
            await expect(page.locator("#price-source")).to_contain_text("2005-01-04")
            await expect(page.locator("#panic-gap")).to_have_text("—")
            await expect(page.locator("#panic-line")).to_have_count(0)
            await expect(page.locator("#panic-comparison-note")).to_contain_text(
                "研究记录晚于所选日期"
            )
            await expect(page.locator("#percentile")).to_have_text("—")
            await expect(page.locator("#eps")).not_to_have_text("—")
            await expect(page.locator("#mean-summary")).to_have_text("近10年平均 —")
            await page.locator("#latest").click()
            await expect(page.locator("#as-of")).to_have_value(data["available_end"])
            await page.locator("[data-mode=fixed]").click()
            await page.locator('[data-years="5"]').click()
            await expect(page.locator('[data-years="5"]')).to_have_attribute("aria-pressed", "true")
            await page.locator("#history-window").select_option("5")
            await expect(page.locator("#percentile-label")).to_have_text("近5年 PE 分位")
            await expect(page.locator("#mean-summary")).to_have_text(
                f"近5年平均 {float(data['selected']['mean_pe']):,.2f}×"
            )
            first_date = await page.locator("#history-rows [data-date]").first.text_content()
            await page.locator("#next-page").click()
            assert (
                await page.locator("#history-rows [data-date]").first.text_content() != first_date
            )
            await page.locator("#previous-page").click()
            assert (
                await page.locator("#history-rows [data-date]").first.text_content() == first_date
            )
            async with page.expect_download() as event:
                await page.locator("#export").click()
            download = await event.value
            download_path = OUTPUT / "web-download.csv"
            await download.save_as(download_path)
            with download_path.open(encoding="utf-8-sig") as source:
                rows = list(csv.DictReader(source))
            assert rows[-1]["trade_date"] == data["available_end"]
            assert float(rows[-1]["pe"]) == float(data["selected"]["pe"])
            assert "band_15x" in rows[-1]
            assert rows[-1]["pe_source"] == "futu"
            assert rows[-1]["mean_pe"] == data["selected"]["mean_pe"]
            assert rows[-1]["mean_points"] == data["selected"]["mean_points"]
            assert rows[-1]["history_years"] == "5"
            assert rows[-1]["basis_status"] == "unconfirmed"
            await page.mouse.move(2, 2)
            await page.screenshot(path=OUTPUT / "web-desktop.png", full_page=True)
            await page.set_viewport_size({"width": 390, "height": 844})
            await page.wait_for_function(
                "document.getElementById('chart').viewBox.baseVal.width < 500"
            )
            await page.screenshot(path=OUTPUT / "web-mobile.png", full_page=True)
            assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
            await expect(page.locator("#band-rows tr")).to_have_count(
                len(data["selected"]["fixed"]) + 1
            )
            await page.locator("#method-details summary").click()
            await expect(page.locator("#basis-note")).to_be_visible()
            additional_indices = await check_additional_indices(page, extra)
            await page.locator("#panic-overview summary").click()
            await page.locator('#panic-overview-rows a[href="/?index=csi300"]').click()
            await expect(page.locator("#index-select")).to_have_value("csi300")
            await page.locator("#index-select").select_option(data["index"]["key"])
            await expect(page.locator("#price")).to_have_text(display(data["selected"]["price"]))
            await expect(page.locator("#code")).to_have_text(data["index"]["code"])
            assert not failures, failures
            report = {
                "browser": "WebKit",
                "status": "passed",
                "latest_date": data["available_end"],
                "coverage": data["coverage"],
                "csv_rows": len(rows),
                "additional_indices": additional_indices,
                "checks": [
                    "live_values",
                    "fixed_bands",
                    "rolling_mean_pe_and_points",
                    "annualized_price_return_with_chart_window_and_as_of",
                    "price_return_independent_of_pe_window_and_missing_pe",
                    "return_endpoints_match_csv_and_single_date_stays_empty",
                    "quantile_bands",
                    "pe_chart",
                    "keyboard_hover",
                    "chart_windows",
                    "history_windows",
                    "backcast_missing_pe",
                    "earliest_pe",
                    "latest_date",
                    "pagination",
                    "csv_download",
                    "mobile_no_overflow",
                    "method_details",
                    "no_javascript_errors",
                    "index_switch_reload_and_independent_csv",
                    "panic_reference_all_indices_thresholds_samples_and_missing_states",
                    "panic_reference_historical_guard_and_window_independence",
                    "panic_reference_overview_links_json_download_and_mobile",
                    "panic_horizontal_line_units_selected_eps_axis_extent_and_mobile_label",
                ],
            }
            (OUTPUT / "web-validation.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(json.dumps(report, ensure_ascii=False, indent=2))
        except Exception:
            await page.screenshot(path=OUTPUT / "web-error.png", full_page=True)
            print("Browser errors:", failures)
            raise
        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
