import json
from copy import deepcopy
from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from index_valuation.archive import json_text
from index_valuation.domain import IndexSpec
from index_valuation.index_series import method_for
from index_valuation.notification_digest import build_digest
from index_valuation.panic_lines import enrich_panic_lines

SPEC = IndexSpec("test", "沪深300", "000300", "SH.000300", band_min_observations=2)
RECEIPT = {
    "batch_id": "batch-1",
    "status": "finished",
    "started_at": "2024-01-02T17:00:00+08:00",
    "finished_at": "2024-01-02T17:05:00+08:00",
    "exit_code": 0,
    "markets": ["CN", "HK"],
    "run_ids": ["run-1"],
}


def make_report(pe="15"):
    points = [
        {
            "trade_date": day,
            "price": Decimal("1000"),
            "pe": Decimal(value),
            "implied_eps": Decimal("50"),
            "pe_source": "futu",
            "pe_basis": "futu_valuation_pe",
            "pe_usable": True,
            "phase": "published",
        }
        for day, value in (
            (date(2019, 1, 2), "10"),
            (date(2020, 1, 2), "20"),
            (date(2024, 1, 2), pe),
        )
    ]
    latest = enrich_panic_lines(SPEC, points)[-1]
    latest.update(percentile=Decimal("50"), history_count=2, history_span_short=False)
    report = {
        "mode": "direct_index",
        "index": SPEC.key,
        "run_id": "run-1",
        "method": method_for(SPEC),
        "status": "collected",
        "last_date": date(2024, 1, 2),
        "latest_complete_date": date(2024, 1, 2),
        "latest": latest,
        "valuation_signal": latest["valuation_signal"],
        "collection": {
            "status": "partial",
            "end": date(2024, 1, 2),
            "last_session": {"date": date(2024, 1, 2)},
            "sources": {
                "futu_price": {"status": "ok", "last_date": date(2024, 1, 2)},
                "futu_pe": {"status": "ok", "last_date": date(2024, 1, 2)},
            },
        },
    }
    # Journals contain JSON scalars, not the in-memory Decimal/date objects.
    return json.loads(json_text(report))


def digest(report=None, *, specs=None, receipt=None):
    return build_digest(
        specs or {SPEC.key: SPEC},
        receipt or RECEIPT,
        [report] if report is not None else [],
    )


@pytest.mark.parametrize(("pe", "label"), [("11", "恐慌"), ("15", "普通"), ("19", "贪婪")])
def test_frozen_report_reuses_inclusive_signal_rules_without_trade_instructions(pe, label):
    report = make_report(pe)
    before = deepcopy(report)
    text = digest(report)
    assert "已覆盖目标交易日" in text
    assert f"PE {pe}.00" in text and "历史分位 50.0%" in text
    assert f"估值状态：{label}（P10 11.00 / P90 19.00）" in text
    assert "01-02 17:00 → 01-02 17:05" in text
    assert not any(word in text for word in ("持续买入", "继续持有", "减仓离场"))
    assert report == before


def test_exit_zero_and_collected_do_not_turn_old_data_into_latest_success():
    report = make_report("10")
    report["collection"]["last_session"]["date"] = "2024-01-03"
    text = digest(report)
    assert "落后目标交易日 2024-01-03" in text
    assert "截至 2024-01-02 历史状态：恐慌" in text
    assert "已覆盖目标交易日" not in text
    assert "主源PE本轮截至 2024-01-02" in text


def test_unused_sources_and_nonprimary_pe_capabilities_are_not_reported_as_outages():
    report = make_report()
    report["collection"]["sources"].update(
        {
            "nasdaq_price": {"status": "unsupported", "missing_sessions": 11},
            "fred_pe": {"status": "ok", "rows": 0, "missing_sessions": 11},
            "futu_pe": {"status": "ok", "missing_sessions": 3},
            "fred_price": {"status": "ok", "missing_sessions": 2},
        }
    )
    text = digest(report)
    assert "Nasdaq 官方价格缺" not in text
    assert "FREDPE缺" not in text
    assert "富途 OpenDPE缺 3 个交易日" in text
    assert "FRED价格缺 2 个交易日" in text


def test_expected_date_comes_from_trading_calendar_not_requested_end():
    report = make_report()
    report["collection"]["end"] = "2024-01-07"
    text = digest(report)
    assert "已覆盖目标交易日" in text
    assert "落后目标" not in text


def test_missing_calendar_keeps_freshness_unknown_even_with_complete_data():
    report = make_report()
    report["collection"].pop("last_session")
    text = digest(report)
    assert "交易日历目标缺失，新鲜度未知" in text
    assert "截至 2024-01-02 历史状态：普通" in text
    assert "已覆盖目标交易日" not in text


def test_missing_report_has_no_completion_evidence_and_market_scope_is_respected():
    us = replace(SPEC, key="us", name="标普500", quote_code="US.SPX")
    text = digest(specs={SPEC.key: SPEC, us.key: us})
    assert "沪深300｜本轮无完成证据" in text
    assert "标普500" not in text
    assert "PE 15" not in text


@pytest.mark.parametrize("has_report", [False, True])
def test_paused_index_does_not_show_archived_valuation_signal(has_report):
    spec = replace(SPEC, collection_enabled=False)
    report = make_report("10") if has_report else None
    if report:
        report["status"] = "blocked"
    text = digest(report, specs={spec.key: spec})
    assert "采集暂停" in text
    assert "本轮已记录暂停" in text if has_report else "本轮无完成证据" in text
    assert "PE 10" not in text
    assert "估值状态：恐慌" not in text


@pytest.mark.parametrize("change", ["method", "date", "source", "threshold", "signal", "top"])
def test_inconsistent_signal_is_never_republished_as_a_valuation_conclusion(change):
    report = make_report("10")
    detail = report["latest"]["valuation_signal"]
    if change == "method":
        report["method"] = "other-method"
    elif change == "date":
        detail["as_of"] = "2023-01-02"
    elif change == "source":
        report["latest"]["pe_source"] = "csindex"
    elif change == "threshold":
        detail["lower_pe"] = "8"
        report["valuation_signal"]["lower_pe"] = "8"
    elif change == "signal":
        detail["signal"] = "greed"
        report["valuation_signal"]["signal"] = "greed"
    else:
        report["valuation_signal"]["pe"] = "99"
    text = digest(report)
    assert "估值状态：暂无判断" in text
    assert "估值状态：恐慌" not in text and "估值状态：贪婪" not in text


def test_short_history_has_no_signal_but_available_rank_has_an_explicit_caveat():
    report = make_report()
    for detail in (report["latest"]["valuation_signal"], report["valuation_signal"]):
        detail.update(status="insufficient_history", signal="unknown", history_complete=False)
    report["latest"]["history_span_short"] = True
    text = digest(report)
    assert "历史分位 50.0%（样本区间不足）" in text
    assert "估值状态：暂无判断" in text
    assert "五年跨度及最少样本要求" in text


def test_source_ok_with_missing_sessions_and_total_return_failure_are_reported_safely():
    report = make_report()
    report["collection"]["sources"]["futu_pe"].update(missing_sessions=3)
    report["collection"]["sources"]["csindex_pe"] = {
        "status": "failed",
        "error": "PRIVATE_TOKEN @ /Users/private/.env",
    }
    report["total_return_collection"] = {
        "status": "failed",
        "error": "PRIVATE_TOKEN",
        "report_path": "/Users/private/data.json",
        "sources": {"csindex": {"status": "failed", "error": "PRIVATE_TOKEN"}},
    }
    text = digest(report)
    assert "富途 OpenDPE缺 3 个交易日" in text
    assert "中证官方PE请求失败" in text
    assert "分红再投资采集失败" in text
    assert "全收益：中证官方请求失败" in text
    assert "PRIVATE_TOKEN" not in text and "/Users/" not in text


def test_missing_eps_does_not_hide_valid_pe_signal():
    report = make_report("10")
    report["latest"]["implied_eps"] = None
    report["latest_complete_date"] = "2024-01-01"
    text = digest(report)
    assert "估值状态：恐慌" in text
    assert "EPS/band 完整至 2024-01-01" in text


def test_timeout_and_failed_reports_do_not_fall_back_to_other_results():
    receipt = {**RECEIPT, "status": "timed_out", "exit_code": 124}
    report = {"index": SPEC.key, "status": "failed", "error": "PRIVATE_ERROR"}
    text = digest(report, receipt=receipt)
    assert "执行超时" in text and "处理失败" in text
    assert "无可核对的估值日期；新鲜度未知" in text
    assert "估值状态：暂无判断" in text
    assert "PRIVATE_ERROR" not in text


def test_data_after_calendar_target_is_not_used_for_a_signal():
    report = make_report("10")
    report["collection"]["last_session"]["date"] = "2024-01-01"
    text = digest(report)
    assert "日期待核对" in text
    assert "估值状态：暂无判断" in text
    assert "估值状态：恐慌" not in text
