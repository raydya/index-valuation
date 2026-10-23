"""Valuation tables and text previews from one batch's frozen reports, with no I/O."""

from datetime import date, datetime

from index_valuation.domain import IndexSpec, decimal_or_none
from index_valuation.index_series import method_for, valuation_profile
from index_valuation.market_overview import build_market_overview

_SOURCES = {
    "futu": "富途 OpenD",
    "futorest": "富途 REST",
    "csindex": "中证官方",
    "cnindex": "国证官方",
    "nasdaq": "Nasdaq 官方",
    "fred": "FRED",
    "baostock": "BaoStock",
    "eastmoney": "东方财富",
    "hsi": "恒生官方",
    "danjuan": "雪球基金",
    "yahoo": "Yahoo",
    "google": "Google",
}


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _day(value):
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    return None


def _positive_integer(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def _number(value, places=2):
    number = decimal_or_none(value)
    return f"{number:.{places}f}" if number is not None else "暂无"


def _detail(value):
    result = dict(_mapping(value))
    for field in ("as_of", "history_start", "history_end"):
        result[field] = _day(result.get(field))
    for field in ("pe", "lower_pe", "upper_pe"):
        result[field] = decimal_or_none(result.get(field))
    return result


def _valuation(spec, report, day):
    point = dict(_mapping(report.get("latest")))
    point["trade_date"] = day
    point["pe"] = decimal_or_none(point.get("pe"))
    detail = _detail(point.get("valuation_signal", report.get("valuation_signal")))
    point["valuation_signal"] = detail
    item = build_market_overview({spec.key: spec}, {spec.key: point})["items"][0]
    profile = valuation_profile(spec)
    same_method = report.get("method") == method_for(spec) and point.get(
        "method", method_for(spec)
    ) == method_for(spec)
    valid = same_method
    if "valuation_signal" in report:
        valid = valid and detail == _detail(report["valuation_signal"])
    if item["signal"] != "unknown":
        pe, lower, upper = point["pe"], detail["lower_pe"], detail["upper_pe"]
        valid = bool(
            valid
            and pe is not None
            and pe > 0
            and lower is not None
            and upper is not None
            and 0 < lower < upper
            and detail.get("history_complete") is True
            and _positive_integer(detail.get("history_count")) >= spec.band_min_observations
            and detail["history_end"] is not None
            and detail["history_end"] < day
        )
        if valid:
            expected = "panic" if pe <= lower else "greed" if pe >= upper else "normal"
            valid = item["signal"] == expected
    label = item["label"] if valid else "暂无判断"
    pe = point["pe"]
    pe_text = _number(pe) if pe is not None and pe > 0 else "暂无"
    percentile = decimal_or_none(point.get("percentile"))
    percentile_ok = (
        same_method
        and point.get("pe_source") == profile["source"]
        and point.get("pe_basis") == profile["basis"]
        and point.get("pe_usable", True)
        and pe is not None
        and pe > 0
        and percentile is not None
        and 0 <= percentile <= 100
        and _positive_integer(point.get("history_count")) >= spec.band_min_observations
    )
    rank = f"{_number(percentile, 1)}%" if percentile_ok else "暂无"
    return pe_text, rank, label


def _source_issues(sources, *, total_return=False, primary_source=None):
    """Source details retained for the separate 18:00 task summary."""
    issues = []
    for key, raw in _mapping(sources).items():
        source = _mapping(raw)
        provider, _, metric = str(key).rpartition("_")
        if total_return:
            provider, metric = key, ""
        label = _SOURCES.get(provider, "其他来源")
        label += {"price": "价格", "pe": "PE", "pb": "PB", "ps": "PS"}.get(metric, "")
        status = source.get("status")
        if status == "unsupported":
            continue
        missing = _positive_integer(source.get("missing_sessions"))
        if status == "failed":
            issues.append(f"{label}请求失败")
        elif missing and (total_return or metric == "price" or provider == primary_source):
            issues.append(f"{label}缺 {missing} 个交易日")
        elif status == "partial":
            issues.append(f"{label}数据不完整")
    return list(dict.fromkeys(issues))


def _index_row(spec, report):
    name = " ".join(spec.name.split())[:80]
    if not spec.collection_enabled or report.get("status") == "blocked":
        return ["暂无", name, "暂无", "暂无", "暂无判断"]
    latest = _mapping(report.get("latest"))
    collection = _mapping(report.get("collection"))
    target = _day(_mapping(collection.get("last_session")).get("date"))
    day = _day(latest.get("trade_date"))
    if day and (target is None or day <= target):
        pe, rank, label = _valuation(spec, report, day)
    else:
        pe, rank, label = "暂无", "暂无", "暂无判断"
    return [str(day) if day else "暂无", name, pe, rank, label]


def build_notification(specs: dict[str, IndexSpec], receipt: dict, reports: list[dict]) -> dict:
    """Share identical fields and validation between the text preview and native table."""
    markets = receipt.get("markets")
    markets = set(markets) if isinstance(markets, list | tuple) else set()
    selected = {key: spec for key, spec in specs.items() if spec.market in markets}
    by_key = {}
    for report in reports:
        if isinstance(report, dict) and report.get("index") in selected:
            by_key[report["index"]] = report
    rows = [_index_row(spec, by_key.get(key, {})) for key, spec in selected.items()]
    if not rows:
        return {"text": "", "rich_message": None}
    groups = {}
    for day, *row in rows:
        # Color follows the validated valuation signal, not a separate percentile rule.
        marker = {"恐慌": "🟢", "贪婪": "🔴"}.get(row[3])
        if marker:
            if row[2] != "暂无":
                row[2] = f"{marker} {row[2]}"
            row[3] = f"{marker} {row[3]}"
        groups.setdefault(day, []).append(row)
    headers = ["指数", "PE", "历史分位", "估值状态"]
    alignments = ["left", "right", "right", "center"]
    blocks, sections = [], []
    for day, group in groups.items():
        title = f"指数估值：{day}"
        cells = [
            [
                {
                    "text": value,
                    "align": "center" if number == 0 else alignments[column],
                    "valign": "middle",
                    **({"is_header": True} if number == 0 else {}),
                }
                for column, value in enumerate(row)
            ]
            for number, row in enumerate([headers, *group])
        ]
        blocks.extend(
            [
                {"type": "heading", "size": 3, "text": title},
                {
                    "type": "table",
                    "cells": cells,
                    "is_bordered": True,
                    "is_striped": True,
                    "is_compact": True,
                },
            ]
        )
        sections.append("\n".join([title, *(" ".join(row) for row in group)]))
    return {"text": "\n\n".join(sections), "rich_message": {"blocks": blocks}}


def build_digest(specs: dict[str, IndexSpec], receipt: dict, reports: list[dict]) -> str:
    """Plain-text preview with dated headings and four fields per index."""
    return build_notification(specs, receipt, reports)["text"]
