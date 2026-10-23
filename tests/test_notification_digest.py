import json
from copy import deepcopy
from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from index_valuation.archive import json_text
from index_valuation.domain import IndexSpec
from index_valuation.index_series import method_for
from index_valuation.notification_digest import build_digest, build_notification
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


@pytest.mark.parametrize(
    ("pe", "label", "rank"),
    [("11", "🟢 恐慌", "🟢 50.0%"), ("15", "普通", "50.0%"), ("19", "🔴 贪婪", "🔴 50.0%")],
)
def test_frozen_report_reuses_inclusive_signal_rules_without_trade_instructions(pe, label, rank):
    report = make_report(pe)
    before = deepcopy(report)
    text = digest(report)
    assert text == f"指数估值：2024-01-02\n沪深300 {pe}.00 {rank} {label}"
    assert report == before


def test_exit_zero_and_collected_do_not_turn_old_data_into_latest_success():
    report = make_report("10")
    report["collection"]["last_session"]["date"] = "2024-01-03"
    text = digest(report)
    assert text == "指数估值：2024-01-02\n沪深300 10.00 🟢 50.0% 🟢 恐慌"


def test_source_details_do_not_add_extra_commentary():
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
    assert text == "指数估值：2024-01-02\n沪深300 15.00 50.0% 普通"


def test_expected_date_comes_from_trading_calendar_not_requested_end():
    report = make_report()
    report["collection"]["end"] = "2024-01-07"
    text = digest(report)
    assert text == "指数估值：2024-01-02\n沪深300 15.00 50.0% 普通"


def test_missing_calendar_preserves_actual_date_without_asserting_freshness():
    report = make_report()
    report["collection"].pop("last_session")
    text = digest(report)
    assert text == "指数估值：2024-01-02\n沪深300 15.00 50.0% 普通"


def test_missing_report_has_no_completion_evidence_and_market_scope_is_respected():
    us = replace(SPEC, key="us", name="标普500", quote_code="US.SPX")
    text = digest(specs={SPEC.key: SPEC, us.key: us})
    assert text == "指数估值：暂无\n沪深300 暂无 暂无 暂无判断"


def test_multiple_indices_share_one_date_heading():
    other = replace(SPEC, key="other", name="第二指数", quote_code="HK.TEST")
    other_report = make_report("19")
    other_report["index"] = other.key
    text = build_digest({SPEC.key: SPEC, other.key: other}, RECEIPT, [make_report(), other_report])
    assert text == "指数估值：2024-01-02\n沪深300 15.00 50.0% 普通\n第二指数 19.00 🔴 50.0% 🔴 贪婪"


def test_no_matching_market_has_no_message():
    assert digest(make_report(), receipt={**RECEIPT, "markets": []}) == ""
    assert build_notification({SPEC.key: SPEC}, {**RECEIPT, "markets": []}, []) == {
        "text": "",
        "rich_message": None,
    }


def test_rich_table_shares_validated_values_and_keeps_index_names_in_one_cell():
    spec = replace(SPEC, name="沪深 300 <指数>")
    message = build_notification({spec.key: spec}, RECEIPT, [make_report("11")])
    blocks = message["rich_message"]["blocks"]
    assert len(blocks) == 2
    assert blocks[0] == {"type": "heading", "size": 3, "text": "指数估值：2024-01-02"}
    table = blocks[1]
    assert table["type"] == "table"
    assert table["is_bordered"] and table["is_striped"] and table["is_compact"]
    header, row = table["cells"]
    assert [cell["text"] for cell in header] == ["指数", "PE", "历史分位", "估值状态"]
    assert all(cell["is_header"] for cell in header)
    assert [cell["text"] for cell in row] == [
        "沪深 300 <指数>",
        "11.00",
        "🟢 50.0%",
        "🟢 恐慌",
    ]
    assert row[1]["align"] == row[2]["align"] == "right"
    assert " ".join(cell["text"] for cell in row) == message["text"].splitlines()[1]


def test_mixed_or_unknown_dates_are_never_placed_under_another_data_date():
    older = replace(SPEC, key="older", name="旧日期指数")
    paused = replace(SPEC, key="paused", name="暂停指数", collection_enabled=False)
    old_report = make_report()
    old_report["index"] = older.key
    old_report["latest"]["trade_date"] = "2024-01-01"
    message = build_notification(
        {SPEC.key: SPEC, older.key: older, paused.key: paused},
        RECEIPT,
        [make_report(), old_report],
    )
    blocks = message["rich_message"]["blocks"]
    assert [block["type"] for block in blocks] == ["heading", "table"] * 3
    assert [block["text"] for block in blocks[::2]] == [
        "指数估值：2024-01-02",
        "指数估值：2024-01-01",
        "指数估值：暂无",
    ]
    assert [block["cells"][1][0]["text"] for block in blocks[1::2]] == [
        "沪深300",
        "旧日期指数",
        "暂停指数",
    ]
    assert all(len(row) == 4 for block in blocks[1::2] for row in block["cells"])


def test_color_uses_the_validated_signal_and_never_marks_missing_rank():
    report = make_report("19")
    report["latest"]["percentile"] = None
    assert digest(report) == "指数估值：2024-01-02\n沪深300 19.00 暂无 🔴 贪婪"
    report = make_report("15")
    report["latest"]["percentile"] = "99"
    assert digest(report) == "指数估值：2024-01-02\n沪深300 15.00 99.0% 普通"


@pytest.mark.parametrize("has_report", [False, True])
def test_paused_index_does_not_show_archived_valuation_signal(has_report):
    spec = replace(SPEC, collection_enabled=False)
    report = make_report("10") if has_report else None
    if report:
        report["status"] = "blocked"
    text = digest(report, specs={spec.key: spec})
    assert text == "指数估值：暂无\n沪深300 暂无 暂无 暂无判断"


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
    assert text.split()[-1] == "暂无判断"
    assert len(text.splitlines()) == 2
    assert "🟢" not in text and "🔴" not in text


def test_short_history_preserves_available_rank_without_adding_explanations():
    report = make_report()
    for detail in (report["latest"]["valuation_signal"], report["valuation_signal"]):
        detail.update(status="insufficient_history", signal="unknown", history_complete=False)
    report["latest"]["history_span_short"] = True
    text = digest(report)
    assert text == "指数估值：2024-01-02\n沪深300 15.00 50.0% 暂无判断"


def test_source_and_total_return_failures_stay_out_of_the_notification():
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
    assert text == "指数估值：2024-01-02\n沪深300 15.00 50.0% 普通"


def test_missing_eps_does_not_hide_valid_pe_signal():
    report = make_report("10")
    report["latest"]["implied_eps"] = None
    report["latest_complete_date"] = "2024-01-01"
    text = digest(report)
    assert text == "指数估值：2024-01-02\n沪深300 10.00 🟢 50.0% 🟢 恐慌"


def test_timeout_and_failed_reports_do_not_fall_back_to_other_results():
    receipt = {**RECEIPT, "status": "timed_out", "exit_code": 124}
    report = {"index": SPEC.key, "status": "failed", "error": "PRIVATE_ERROR"}
    text = digest(report, receipt=receipt)
    assert text == "指数估值：暂无\n沪深300 暂无 暂无 暂无判断"


def test_data_after_calendar_target_is_not_used_for_a_signal():
    report = make_report("10")
    report["collection"]["last_session"]["date"] = "2024-01-01"
    text = digest(report)
    assert text == "指数估值：2024-01-02\n沪深300 暂无 暂无 暂无判断"


@pytest.mark.parametrize("pe", [None, "0", "-1"])
def test_missing_or_invalid_pe_keeps_date_heading_without_a_signal(pe):
    report = make_report()
    report["latest"]["pe"] = pe
    assert digest(report) == "指数估值：2024-01-02\n沪深300 暂无 暂无 暂无判断"


@pytest.mark.parametrize("percentile", [None, "-1", "101"])
def test_unavailable_rank_does_not_become_a_percentage(percentile):
    report = make_report()
    report["latest"]["percentile"] = percentile
    assert digest(report) == "指数估值：2024-01-02\n沪深300 15.00 暂无 普通"
