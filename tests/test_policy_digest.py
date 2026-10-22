import json
from copy import deepcopy
from datetime import date, datetime
from pathlib import Path

import pytest

from index_valuation.policy_digest import (
    AUTHORITIES,
    ZONE,
    render_digest,
    result_schema,
    validate_result,
)

DAY = date(2026, 10, 22)
WINDOW = {
    "report_date": DAY,
    "window_start": datetime(2026, 10, 21, 16, tzinfo=ZONE),
    "cutoff_at": datetime(2026, 10, 22, 16, tzinfo=ZONE),
}


@pytest.fixture
def result():
    return {
        "status": "completed",
        "summary": "发现一项政策，作用主要在降低融资成本；市场反应仍需观察。",
        "overall_impact": "positive",
        "major_policy_signal": False,
        "events": [
            {
                "title": "测试政策：降低融资成本",
                "policy_summary": "发布测试融资安排，明确适用对象和实施日期（离线夹具）。",
                "agency": "人民银行",
                "published_date": "2026-10-22",
                "published_at": "2026-10-22T10:00:00+08:00",
                "effective_date": "2026-10-23",
                "stage": "issued",
                "scope": "nationwide",
                "importance": "material",
                "impact": "positive",
                "affected_assets": ["A股", "国债"],
                "mechanism": "融资成本下降可能改善企业现金流；这是传导机制分析。",
                "comparison_to_924": "单项利率措施，尚未形成跨部门政策组合。",
                "confidence": "medium",
                "source_url": "https://www.pbc.gov.cn/policy/example.html",
                "source_title": "政策通知（离线测试夹具）",
            }
        ],
        "coverage": [
            {
                "authority": key,
                "status": "checked",
                "urls": [
                    "https://www.gov.cn/zhengce/"
                    if key == "government"
                    else f"https://www.{key}.gov.cn/policy/"
                ],
                "note": "已检查发布时间及政策内容（离线测试夹具）。",
            }
            for key in AUTHORITIES
        ],
        "issues": [],
    }


def validate(payload):
    return validate_result(payload, **WINDOW)


def test_render_preserves_evidence_and_separates_inferences(result):
    original = deepcopy(result)
    clean = validate(result)
    text = render_digest(clean, **WINDOW)
    assert clean["status"] == "completed"
    assert result == original
    assert "事实：人民银行" in text
    assert "政策事实：发布测试融资安排，明确适用对象和实施日期" in text
    assert "正式发布；生效日 2026-10-23" in text
    assert "传导机制（推断）" in text
    assert "总体影响（分析）：偏正面" in text
    assert "https://www.pbc.gov.cn/policy/example.html" in text
    assert "已检查 8/8" in text
    assert "重大政策信号：未确认" in text


@pytest.mark.parametrize("kind", ["missing", "unavailable"])
def test_missing_authority_downgrades_completion(result, kind):
    if kind == "missing":
        result["coverage"].pop()
    else:
        result["coverage"][-1]["status"] = "unavailable"
        result["coverage"][-1]["urls"] = []
    clean = validate(result)
    assert clean["status"] == "partial"
    assert "商务部" in " ".join(clean["issues"])
    assert validate(clean) == clean
    assert "已检查 7/8" in render_digest(clean, **WINDOW)


def test_no_events_does_not_claim_absolute_absence(result):
    result["events"] = []
    result["summary"] = "今天绝对没有任何政策。"
    clean = validate(result)
    assert clean["status"] == "completed"
    assert clean["overall_impact"] == "uncertain"
    text = render_digest(clean, **WINDOW)
    assert "截至北京时间 2026-10-22 16:00，在已检查来源中未发现" in text
    assert "绝对没有" not in text
    assert "总体影响" not in text


def test_no_usable_search_is_failure_not_no_news(result):
    result["coverage"] = []
    result["events"] = []
    result["summary"] = "今天没有政策。"
    clean = validate(result)
    assert clean["status"] == "failed"
    text = render_digest(clean, **WINDOW)
    assert "无法判断" in text
    assert "未发现" not in text
    assert "今天没有政策" not in text


def test_failure_cannot_publish_unqualified_no_news(result):
    result.update(status="failed", events=[], summary="未发现政策", overall_impact="neutral")
    clean = validate(result)
    assert clean["overall_impact"] == "uncertain"
    assert "无法判断" in clean["summary"]
    assert "未发现" not in render_digest(clean, **WINDOW)


def test_failure_with_confirmed_events_rejected(result):
    result["status"] = "failed"
    with pytest.raises(ValueError, match="已有有效证据"):
        validate(result)


@pytest.mark.parametrize(
    "url",
    [
        "https://pbc.gov.cn.evil.test/policy",
        "https://fakegov.cn/policy",
        "https://www.pbc.gov.cn@evil.test/policy",
        "https://user:password@www.pbc.gov.cn/policy",
        "file:///etc/passwd",
        "https://www.pbc.gov.cn:5555/policy",
        "https://www.pbc.gov.cn/policy\nhttps://evil.test",
        "https://www.example.com/policy",
    ],
)
def test_nonofficial_or_unsafe_event_urls_rejected(result, url):
    result["events"][0]["source_url"] = url
    with pytest.raises(ValueError):
        validate(result)


@pytest.mark.parametrize(
    "url",
    [
        "https://www.gov.cn/zhengce/example.html",
        "http://www.mof.gov.cn/example.html",
        "https://www.news.cn/politics/example.html",
        "https://www.xinhuanet.com/politics/example.html",
        "https://cpc.people.com.cn/example.html",
    ],
)
def test_official_sources_and_central_meeting_release_allowed(result, url):
    result["events"][0]["source_url"] = url
    assert validate(result)["events"][0]["source_url"] == url


def test_checked_source_requires_actual_official_url(result):
    result["coverage"][0]["urls"] = []
    with pytest.raises(ValueError, match="实际检查 URL"):
        validate(result)
    result["coverage"][0]["urls"] = ["https://example.com/policy"]
    with pytest.raises(ValueError, match="官方"):
        validate(result)


@pytest.mark.parametrize("authority", [key for key in AUTHORITIES if key != "government"])
def test_checked_authority_requires_own_domain(result, authority):
    item = next(item for item in result["coverage"] if item["authority"] == authority)
    item["urls"] = ["https://www.gov.cn/", "https://www.news.cn/politics/"]
    with pytest.raises(ValueError, match="该机构官网"):
        validate(result)
    item["urls"].append(f"https://news.{authority}.gov.cn/example.html")
    assert validate(result)["status"] == "completed"


def test_central_government_coverage_not_replaced_by_department(result):
    result["coverage"][0]["urls"] = ["https://www.pbc.gov.cn/"]
    with pytest.raises(ValueError, match="中央政府.*该机构官网"):
        validate(result)
    result["coverage"][0]["urls"] = ["https://www.news.cn/politics/"]
    assert validate(result)["status"] == "completed"


@pytest.mark.parametrize("value", [None, "", "x" * 501])
def test_policy_fact_summary_is_required_and_bounded(result, value):
    result["events"][0]["policy_summary"] = value
    with pytest.raises(ValueError):
        validate(result)


@pytest.mark.parametrize(
    ("published_date", "published_at"),
    [
        ("2026-10-23", "2026-10-23T10:00:00+08:00"),
        ("2026-10-22", "2026-10-22T16:00:01+08:00"),
        ("2026-10-21", "2026-10-21T15:59:59+08:00"),
        ("2026-10-20", "2026-10-20T16:00:00+08:00"),
        ("2026-10-21", None),
        ("2026-10-22", "2026-10-22T10:00:00"),
        ("2026-10-22", "2026-10-21T18:00:00+08:00"),
        ("2026-02-30", None),
    ],
)
def test_future_out_of_window_or_unconfirmed_old_events_rejected(
    result, published_date, published_at
):
    result["events"][0].update(published_date=published_date, published_at=published_at)
    with pytest.raises(ValueError):
        validate(result)


def test_previous_evening_supplement_and_utc_conversion(result):
    result["events"][0].update(published_date="2026-10-21", published_at="2026-10-21T12:00:00Z")
    clean = validate(result)
    assert clean["events"][0]["published_at"] == "2026-10-21T20:00:00+08:00"
    assert "前日 16:00 后补充" in render_digest(clean, **WINDOW)


def test_today_without_time_retained_with_explicit_uncertainty(result):
    result["events"][0]["published_at"] = None
    clean = validate(result)
    assert clean["status"] == "partial"
    assert clean["events"][0]["published_at"] is None
    text = render_digest(clean, **WINDOW)
    assert "具体时刻未核" in text
    assert "与截止时点关系待核" in text
    assert validate(clean) == clean


def test_draft_is_not_rendered_as_implemented(result):
    result["events"][0]["stage"] = "draft"
    text = render_digest(result, **WINDOW)
    assert "征求意见/草案" in text
    assert "已生效" not in text


def test_future_effective_date_cannot_claim_already_effective(result):
    result["events"][0]["stage"] = "effective"
    with pytest.raises(ValueError, match="未来生效"):
        validate(result)


def test_major_signal_requires_major_evidence(result):
    result["major_policy_signal"] = True
    with pytest.raises(ValueError, match="重大事件依据"):
        validate(result)
    result["events"][0]["importance"] = "major"
    assert validate(result)["major_policy_signal"] is True
    assert "不将单项措施等同于 924 政策组合" in render_digest(result, **WINDOW)


@pytest.mark.parametrize("change", ["event", "authority", "boolean", "extra", "length"])
def test_duplicate_or_unbounded_payloads_rejected(result, change):
    if change == "event":
        result["events"].append(deepcopy(result["events"][0]))
    elif change == "authority":
        result["coverage"][-1] = deepcopy(result["coverage"][0])
    elif change == "boolean":
        result["major_policy_signal"] = "false"
    elif change == "extra":
        result["buy_recommendation"] = "buy now"
    else:
        result["events"][0]["mechanism"] = "a" * 301
    with pytest.raises(ValueError):
        validate(result)


def test_control_sequences_rejected(result):
    result["summary"] = "Policy\x1b[31m"
    with pytest.raises(ValueError, match="控制字符"):
        validate(result)


def test_report_context_requires_aware_matched_window(result):
    with pytest.raises(ValueError, match="时区"):
        validate_result(result, **(WINDOW | {"cutoff_at": datetime(2026, 10, 22, 16)}))
    with pytest.raises(ValueError, match="报告日期"):
        validate_result(result, **(WINDOW | {"report_date": date(2026, 10, 23)}))


def test_committed_output_schema_matches_validation_and_requires_nullable_fields():
    schema_path = Path(__file__).resolve().parents[1] / "docs/policy-result.schema.json"
    schema = json.loads(schema_path.read_text())
    assert schema == result_schema()
    for model in [schema, *schema["$defs"].values()]:
        assert model["additionalProperties"] is False
        assert set(model["required"]) == set(model["properties"])
    assert "published_at" in schema["$defs"]["PolicyEvent"]["required"]
