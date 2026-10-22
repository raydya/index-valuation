"""Bounded policy evidence and plain-text reporting, independent of search or delivery."""

import re
from datetime import date, datetime
from typing import Annotated, Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StringConstraints

ZONE = ZoneInfo("Asia/Shanghai")
AUTHORITIES = {
    "government": "中央政府",
    "pbc": "人民银行",
    "mof": "财政部",
    "ndrc": "发展改革委",
    "csrc": "证监会",
    "nfra": "金融监管总局",
    "mohurd": "住房城乡建设部",
    "mofcom": "商务部",
}
OFFICIAL_DOMAINS = ("gov.cn", "news.cn", "xinhuanet.com", "cpc.people.com.cn")
AUTHORITY_DOMAINS = {
    "government": ("www.gov.cn", "news.cn", "xinhuanet.com", "cpc.people.com.cn"),
    **{key: (f"{key}.gov.cn",) for key in AUTHORITIES if key != "government"},
}
IMPACT_LABELS = {
    "positive": "偏正面",
    "negative": "偏负面",
    "mixed": "影响分化",
    "neutral": "中性",
    "uncertain": "影响不确定",
}
Impact = Literal["positive", "negative", "mixed", "neutral", "uncertain"]
ShortText = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1)]
Day = Annotated[str, StringConstraints(strict=True, pattern=r"^\d{4}-\d{2}-\d{2}$")]
URL = Annotated[ShortText, Field(max_length=1000)]


class _Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PolicyEvent(_Evidence):
    title: Annotated[ShortText, Field(max_length=120)]
    policy_summary: Annotated[ShortText, Field(max_length=500)]
    agency: Annotated[ShortText, Field(max_length=80)]
    published_date: Day
    published_at: Annotated[ShortText, Field(max_length=40)] | None
    effective_date: Day | None
    stage: Literal["announcement", "draft", "issued", "effective"]
    scope: Literal["nationwide", "sector", "local"]
    importance: Literal["major", "material", "routine"]
    impact: Impact
    affected_assets: Annotated[
        list[Annotated[ShortText, Field(max_length=60)]], Field(min_length=1, max_length=6)
    ]
    mechanism: Annotated[ShortText, Field(max_length=300)]
    comparison_to_924: Annotated[ShortText, Field(max_length=200)]
    confidence: Literal["high", "medium", "low"]
    source_url: URL
    source_title: Annotated[ShortText, Field(max_length=160)]


class SourceCoverage(_Evidence):
    authority: Literal["government", "pbc", "mof", "ndrc", "csrc", "nfra", "mohurd", "mofcom"]
    status: Literal["checked", "unavailable"]
    urls: Annotated[list[URL], Field(max_length=4)]
    note: Annotated[ShortText, Field(max_length=240)]


class PolicyResult(_Evidence):
    status: Literal["completed", "partial", "failed"]
    summary: Annotated[ShortText, Field(max_length=600)]
    overall_impact: Impact
    major_policy_signal: StrictBool
    events: Annotated[list[PolicyEvent], Field(max_length=8)]
    coverage: Annotated[list[SourceCoverage], Field(max_length=8)]
    issues: Annotated[list[Annotated[ShortText, Field(max_length=300)]], Field(max_length=40)]


def result_schema():
    """Strict schema suitable for codex exec --output-schema; all fields are required."""
    return PolicyResult.model_json_schema()


def _official_url(value):
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError:
        raise ValueError("政策来源 URL 端口无效") from None
    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme not in {"https", "http"}
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 80, 443}
        or any(char.isspace() or ord(char) < 32 for char in value)
        or not any(host == domain or host.endswith("." + domain) for domain in OFFICIAL_DOMAINS)
    ):
        raise ValueError("政策来源须使用允许的官方 HTTP(S) URL")


def _authority_url(value, authority):
    host = (urlsplit(value).hostname or "").lower()
    if authority == "government" and host == "gov.cn":
        return True
    return any(
        host == domain or host.endswith("." + domain) for domain in AUTHORITY_DOMAINS[authority]
    )


def _time(value):
    parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
    if not isinstance(parsed, datetime) or parsed.utcoffset() is None:
        raise ValueError("政策时间必须包含时区")
    return parsed.astimezone(ZONE)


def _context(report_date, window_start, cutoff_at):
    start, cutoff = _time(window_start), _time(cutoff_at)
    if not isinstance(report_date, date) or isinstance(report_date, datetime):
        raise ValueError("报告日期必须为 date")
    if start >= cutoff or cutoff.date() != report_date:
        raise ValueError("监测窗口或报告日期不匹配")
    return start, cutoff


def _clean_text(value):
    """Reject control characters instead of forwarding invisible terminal instructions."""
    if isinstance(value, str):
        if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", value):
            raise ValueError("政策报告包含控制字符")
    elif isinstance(value, list):
        for child in value:
            _clean_text(child)
    elif isinstance(value, dict):
        for child in value.values():
            _clean_text(child)


def validate_result(payload, *, report_date: date, window_start: datetime, cutoff_at: datetime):
    """Validate reported evidence; completeness means searched sources, not certain forecasts.

    Missing coverage is preserved as partial/failed. Invalid identities and dates raise
    ValueError so a caller can retain the raw response and publish an explicit failure.
    This validates evidence structure, not that a model actually read the cited page.
    """
    start, cutoff = _context(report_date, window_start, cutoff_at)
    _clean_text(payload)
    clean = PolicyResult.model_validate(payload).model_dump(mode="json")
    issues = list(dict.fromkeys(clean["issues"]))
    coverage = {}
    for item in clean["coverage"]:
        if item["authority"] in coverage:
            raise ValueError("来源覆盖机构不能重复")
        coverage[item["authority"]] = item
        for url in item["urls"]:
            _official_url(url)
        if item["status"] == "checked" and not item["urls"]:
            raise ValueError("已检查的来源须提供实际检查 URL")
        if item["status"] == "checked" and not any(
            _authority_url(url, item["authority"]) for url in item["urls"]
        ):
            raise ValueError(f"{AUTHORITIES[item['authority']]}已检查记录须包含该机构官网 URL")
    for key, label in AUTHORITIES.items():
        item = coverage.get(key)
        if item is None:
            issues.append(f"未提供{label}的检查记录。")
        elif item["status"] == "unavailable":
            issues.append(f"{label}来源未完成检查。")
    seen = set()
    for event in clean["events"]:
        _official_url(event["source_url"])
        published_day = date.fromisoformat(event["published_date"])
        if event["effective_date"] is not None:
            effective_day = date.fromisoformat(event["effective_date"])
            if event["stage"] == "effective" and effective_day > report_date:
                raise ValueError("未来生效政策不能标记为已生效")
        if published_day > report_date or published_day < start.date():
            raise ValueError("政策发布日期不在监测窗口内")
        if event["published_at"] is not None:
            published = _time(event["published_at"])
            if published.date() != published_day or not start <= published <= cutoff:
                raise ValueError("政策发布时间不在窗口内或与发布日期不一致")
            event["published_at"] = published.isoformat()
        elif published_day != report_date:
            raise ValueError("前一日政策须核实具体发布时间，不能仅凭日期确认为窗口事件")
        else:
            issues.append(f"《{event['title']}》仅核实当日日期，具体发布时间与截止时点关系待核。")
        identity = (event["source_url"], event["title"], event["published_date"])
        if identity in seen:
            raise ValueError("政策事件重复")
        seen.add(identity)
    if clean["major_policy_signal"] and not any(
        event["importance"] == "major" for event in clean["events"]
    ):
        raise ValueError("重大政策信号必须有重大事件依据")
    if clean["status"] == "failed" and clean["events"]:
        raise ValueError("检索失败不能包含已确认事件；已有有效证据时应记录部分完成")
    checked = sum(item["status"] == "checked" for item in coverage.values())
    if not checked and not clean["events"]:
        clean["status"] = "failed"
    elif clean["status"] == "completed" and issues:
        clean["status"] = "partial"
    if clean["status"] == "failed":
        clean["summary"] = "本次政策检索失败，无法判断监测窗口内是否存在影响市场的新政策。"
        clean["overall_impact"] = "uncertain"
        clean["major_policy_signal"] = False
    elif not clean["events"]:
        clean["summary"] = (
            f"截至北京时间 {cutoff:%Y-%m-%d %H:%M}，在已检查来源中未发现"
            "已核实、可能影响市场的新增政策。"
        )
        clean["overall_impact"] = "uncertain"
    clean["issues"] = list(dict.fromkeys(issues))
    if len(clean["issues"]) > 40:
        raise ValueError("政策报告待核问题过多，须缩减并核实")
    return clean


def render_digest(payload, *, report_date, window_start, cutoff_at):
    """Render factual stages separately from directional inference; preserve direct URLs."""
    clean = validate_result(
        payload, report_date=report_date, window_start=window_start, cutoff_at=cutoff_at
    )
    start, cutoff = _context(report_date, window_start, cutoff_at)
    status = {"completed": "检查完成", "partial": "部分完成", "failed": "检索失败"}
    lines = [
        f"中国政策日报｜{report_date.isoformat()}",
        f"北京时间 {start:%m-%d %H:%M} 至 {cutoff:%m-%d %H:%M}",
        f"状态：{status[clean['status']]}",
        clean["summary"],
    ]
    if clean["events"]:
        lines.extend(
            [
                f"总体影响（分析）：{IMPACT_LABELS[clean['overall_impact']]}",
                "重大政策信号："
                + ("发现；与 924 的差异见下文" if clean["major_policy_signal"] else "未确认")
                + "。不将单项措施等同于 924 政策组合。",
            ]
        )
    stage_labels = {
        "announcement": "宣布方向或措施",
        "draft": "征求意见/草案",
        "issued": "正式发布",
        "effective": "已生效",
    }
    scope_labels = {"nationwide": "全国", "sector": "行业", "local": "地方"}
    confidence_labels = {"high": "高", "medium": "中", "low": "低"}
    importance_labels = {"major": "重大", "material": "重要", "routine": "常规"}
    for number, event in enumerate(clean["events"], 1):
        published = (
            _time(event["published_at"]).strftime("%m-%d %H:%M")
            if event["published_at"]
            else event["published_date"] + "（具体时刻未核）"
        )
        supplement = "；前日 16:00 后补充" if event["published_date"] < str(report_date) else ""
        effective = event["effective_date"] or "未明确"
        lines.extend(
            [
                "",
                f"{number}. {event['title']}",
                f"政策事实：{event['policy_summary']}",
                f"事实：{event['agency']}；发布 {published}{supplement}；"
                f"{stage_labels[event['stage']]}；生效日 {effective}。",
                f"范围：{scope_labels[event['scope']]}；重要性："
                f"{importance_labels[event['importance']]}。",
                f"影响（分析）：{IMPACT_LABELS[event['impact']]}；"
                f"涉及 {'、'.join(event['affected_assets'])}；"
                f"判断置信度 {confidence_labels[event['confidence']]}。",
                f"传导机制（推断）：{event['mechanism']}",
                f"与 924 比较：{event['comparison_to_924']}",
                f"官方来源：{event['source_title']}\n{event['source_url']}",
            ]
        )
    checked = [
        AUTHORITIES[item["authority"]] for item in clean["coverage"] if item["status"] == "checked"
    ]
    lines.extend(["", f"来源覆盖：已检查 {len(checked)}/8：{'、'.join(checked) or '无'}。"])
    if clean["issues"]:
        lines.append("检索缺口/待核：" + "；".join(clean["issues"]))
    return "\n".join(lines)
