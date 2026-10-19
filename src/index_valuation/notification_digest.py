"""Plain-text notifications from one batch's frozen reports, with no I/O."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

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
_BATCH_STATUS = {
    "finished": "流程完成",
    "partial": "部分完成",
    "failed": "执行失败",
    "timed_out": "执行超时",
    "running": "仍在执行",
}
_RESULT_STATUS = {
    "collected": "已返回结果",
    "partial": "部分完成",
    "failed": "处理失败",
    "skipped": "跳过采集",
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


def _time(value):
    try:
        parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
        if isinstance(parsed, datetime) and parsed.tzinfo is not None:
            return parsed.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%m-%d %H:%M")
    except ValueError:
        pass
    return "未知"


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


def _valuation(spec, report, day, historical):
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
    if not valid:
        reason = "估值信号与本轮数据或当前口径不一致"
    elif item["signal"] == "unknown":
        reason = item["reason"] or "暂无可用估值信号"
    else:
        reason = f"P10 {_number(item['lower_pe'])} / P90 {_number(item['upper_pe'])}"
    pe = point["pe"]
    pe_text = _number(pe) if pe is not None and pe > 0 else "暂无有效值"
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
    rank = f"历史分位 {_number(percentile, 1)}%" if percentile_ok else "历史分位暂无"
    if percentile_ok and point.get("history_span_short"):
        rank += "（样本区间不足）"
    prefix = f"截至 {day} 历史状态" if historical else "估值状态"
    return f"PE {pe_text}｜{rank}｜{prefix}：{label}（{reason}）"


def _source_issues(sources, *, total_return=False, primary_source=None):
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


def _index_lines(spec, report):
    name = " ".join(spec.name.split())[:80]
    if not spec.collection_enabled or report.get("status") == "blocked":
        evidence = "本轮已记录暂停" if report else "本轮无完成证据"
        return [f"{name}｜采集暂停（{evidence}）；不作当前估值判断"]
    if not report:
        return [f"{name}｜本轮无完成证据；不以旧数据代替本轮结果"]
    latest = _mapping(report.get("latest"))
    collection = _mapping(report.get("collection"))
    target = _day(_mapping(collection.get("last_session")).get("date"))
    day = _day(latest.get("trade_date"))
    historical = not target or not day or day != target
    if day is None:
        freshness = "无可核对的估值日期；新鲜度未知"
    elif target is None:
        freshness = f"数据 {day}；交易日历目标缺失，新鲜度未知"
    elif day < target:
        freshness = f"数据 {day}；落后目标交易日 {target}"
    elif day > target:
        freshness = f"数据 {day}；晚于目标交易日 {target}，日期待核对"
    else:
        freshness = f"数据 {day}；已覆盖目标交易日"
    lines = [f"{name}｜{freshness}"]
    if day and (target is None or day <= target):
        lines.append(_valuation(spec, report, day, historical))
    else:
        lines.append("估值状态：暂无判断")
    issues = [_RESULT_STATUS.get(report.get("status"), "结果状态待核对")]
    complete = _day(report.get("latest_complete_date"))
    if latest.get("implied_eps") is None or complete != day:
        issues.append(f"EPS/band 完整至 {complete}" if complete else "EPS/band 未完整")
    if collection.get("status") == "failed":
        issues.append("本轮估值采集失败，展示已有数据")
    issues.extend(_source_issues(collection.get("sources"), primary_source=spec.valuation_source))
    primary_sources = _mapping(collection.get("sources"))
    if target:
        for metric, label in (("price", "价格"), ("pe", "PE")):
            source = _mapping(primary_sources.get(f"{spec.valuation_source}_{metric}"))
            last = _day(source.get("last_date"))
            if source and source.get("status") != "unsupported" and last != target:
                issues.append(f"主源{label}本轮截至 {last}" if last else f"主源{label}本轮未取到")
    quarantined = _positive_integer(collection.get("quarantined_count"))
    if quarantined:
        issues.append(f"隔离 {quarantined} 条异常记录")
    returns = _mapping(report.get("total_return_collection"))
    if returns.get("status") in {"failed", "blocked", "partial"}:
        issues.append(
            "分红再投资采集失败" if returns["status"] == "failed" else "分红再投资数据有缺口"
        )
    return_issues = _source_issues(returns.get("sources"), total_return=True)
    if return_issues:
        issues.append("全收益：" + "、".join(return_issues))
    elif _positive_integer(returns.get("missing_primary_sessions")):
        issues.append(f"全收益主源缺 {returns['missing_primary_sessions']} 个交易日")
    lines.append("本轮：" + "；".join(dict.fromkeys(issues)))
    return lines


def build_digest(specs: dict[str, IndexSpec], receipt: dict, reports: list[dict]) -> str:
    """Summarize this batch only; execution, freshness and valuation stay separate."""
    markets = receipt.get("markets")
    markets = set(markets) if isinstance(markets, list | tuple) else set()
    selected = {key: spec for key, spec in specs.items() if spec.market in markets}
    title = "、".join(
        label for key, label in (("CN", "A股"), ("HK", "港股"), ("US", "美股")) if key in markets
    )
    lines = [
        f"指数估值更新｜{title or '市场范围未知'}｜"
        + _BATCH_STATUS.get(receipt.get("status"), "状态待核对"),
        f"执行时间 {_time(receipt.get('started_at'))} → {_time(receipt.get('finished_at'))}"
        "（北京时间）",
    ]
    by_key = {}
    for report in reports:
        if isinstance(report, dict) and report.get("index") in selected:
            by_key[report["index"]] = report
    for key, spec in selected.items():
        lines.extend(("", *_index_lines(spec, by_key.get(key, {}))))
    if not selected:
        lines.extend(("", "没有与本轮市场范围匹配的指数，未采用其他市场数据。"))
    lines.extend(("", "估值状态沿用此前五年同口径 PE 的 P10/P90；样本不足不判断。"))
    return "\n".join(lines)
