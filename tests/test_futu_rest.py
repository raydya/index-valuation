import json
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest

from index_valuation.archive import Archive
from index_valuation.domain import DataError, IndexSpec
from index_valuation.providers.futu_rest import (
    FutuRestHistory,
    FutuRestSourceError,
    parse_history_bar,
    parse_valuation_history,
)

SPEC = IndexSpec(
    "sp500",
    "S&P 500",
    "SPX",
    "US..SPX",
    currency="USD",
    timezone="America/New_York",
    close_time="16:15",
    futorest_symbol="US..SPX",
)
IDENTITY = {"code": "US..SPX", "name": "S&P 500 Index", "stock_type": "IDX", "exchange": "US"}
START, END = date(2020, 3, 5), date(2020, 3, 10)
NOW = datetime(2020, 3, 11, 1, tzinfo=UTC)


@pytest.fixture(autouse=True)
def request_waits(monkeypatch):
    waits = []
    monkeypatch.setattr("index_valuation.providers.futu_rest.clock.sleep", waits.append)
    monkeypatch.setattr(FutuRestHistory, "_last_request_at", 0.0)
    monkeypatch.setattr(FutuRestHistory, "_not_before", 0.0)
    return waits


def timestamp(day):
    return datetime.combine(day, time(), ZoneInfo(SPEC.timezone))


def bar(day=END, **fields):
    label = timestamp(day)
    return {
        "date": int(day.strftime("%Y%m%d")),
        "time_key": int(label.timestamp() * 1000),
        "time_zone": int(label.utcoffset().total_seconds() / 3600),
        "name": IDENTITY["name"],
        "close": "3000.123456789",
        "pe_ratio": 0,
        **fields,
    }


def valuation(day=END, value="25.123456789"):
    return {"time_str": day.isoformat(), "time": int(timestamp(day).timestamp()), "value": value}


def response(data, **extra):
    return httpx.Response(200, json={"ret_code": 0, "data": data, **extra})


def run(tmp_path, *, history=None, valuations=None, identities=None, start=START, end=END):
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path.endswith("stock-basicinfo"):
            assert request.method == "POST"
            assert json.loads(request.content) == {"code_list": ["US..SPX"]}
            return response({"basic_list": [IDENTITY] if identities is None else identities})
        if request.url.path.endswith("history-kline"):
            assert request.url.params["ktype"] == "2"
            assert request.url.params["autype"] == "0"
            assert request.url.params["num"] == "370"
            if history:
                return history(request)
            return response({"kline_list": [bar()]}, pagination={"has_more": False})
        assert request.url.path == "/api/v1.0/quote/US..SPX/valuation/detail"
        assert request.url.params["interval_type"] == "10"
        metric = int(request.url.params["valuation_type"])
        if valuations:
            return valuations(request, metric)
        return response({"valuation_type": metric, "trend": {"historical_items": [valuation()]}})

    with httpx.Client(
        base_url="https://example.test", transport=httpx.MockTransport(handler)
    ) as client:
        result = FutuRestHistory(Archive(tmp_path), client).index_history(SPEC, start, end)
    return result, calls


def test_rest_prices_and_metrics_keep_decimal_and_distinct_provenance(tmp_path):
    result, calls = run(tmp_path)
    assert len(calls) == 5 and result.failures == []
    price = result.observations[0]
    assert price.source == "futorest" and price.price == Decimal("3000.123456789")
    assert price.eps_ttm is price.pe_ttm is None
    assert price.raw["pe_ratio"] == 0 and price.raw["identity"] == IDENTITY
    assert price.quote_at.hour == 16 and price.quote_at.minute == 15
    assert {row.metric for row in result.valuations} == {"pe", "pb", "ps"}
    for row in result.valuations:
        assert row.basis == f"futorest_valuation_{row.metric}"
        assert row.value == Decimal("25.123456789")
        assert Path(row.artifact_path).exists()
    assert len(list(tmp_path.rglob("*.json"))) == 5


def test_json_numeric_prices_and_valuations_have_jsonb_safe_raw_provenance(tmp_path):
    price_text, pe_text = "3000.1234567890123456789", "25.1234567890123456789"

    def history(_request):
        payload = {
            "ret_code": 0,
            "data": {"kline_list": [bar(close=price_text)]},
            "pagination": {"has_more": False},
        }
        return httpx.Response(
            200, content=json.dumps(payload).replace(f'"{price_text}"', price_text)
        )

    def valuations(_request, metric):
        payload = {
            "ret_code": 0,
            "data": {
                "valuation_type": metric,
                "trend": {"historical_items": [valuation(value=pe_text)]},
            },
        }
        return httpx.Response(200, content=json.dumps(payload).replace(f'"{pe_text}"', pe_text))

    result, _ = run(tmp_path, history=history, valuations=valuations)
    assert not result.failures
    assert result.observations[0].price == Decimal(price_text)
    assert result.observations[0].raw["close"] == price_text
    for row in result.valuations:
        assert row.value == Decimal(pe_text)
        assert row.raw["value"] == pe_text
    # Match the repository's ordinary JSONB encoder; no custom default hook.
    json.dumps([row.raw for row in result.observations + result.valuations], allow_nan=False)


@pytest.mark.parametrize("day", [date(1957, 3, 4), date(2020, 3, 6), date(2020, 3, 9)])
def test_date_labels_follow_new_york_dst_and_support_pre_epoch_dates(day):
    row = parse_history_bar(bar(day), SPEC, NOW, "raw", identity=IDENTITY, symbol=SPEC.quote_code)
    assert row.trade_date == day
    assert row.quote_at.utcoffset() == timestamp(day).utcoffset()


@pytest.mark.parametrize(
    "fields,match",
    [
        ({"date": 20200309}, "date disagree"),
        ({"time_zone": -5}, "timezone"),
        ({"close": 0}, "invalid or unfinished"),
        ({"name": "Nasdaq 100 Index"}, "different index"),
        ({"code": "US.SPY"}, "different security"),
        ({"time_key": "2020-03-10"}, "timestamp"),
    ],
)
def test_bad_price_fields_are_rejected(fields, match):
    with pytest.raises(DataError, match=match):
        parse_history_bar(
            bar(**fields), SPEC, NOW, "raw", identity=IDENTITY, symbol=SPEC.quote_code
        )


def test_current_session_price_and_pe_are_not_admitted_before_close():
    observed = datetime(2020, 3, 10, 16, tzinfo=UTC)
    with pytest.raises(DataError, match="unfinished"):
        parse_history_bar(bar(), SPEC, observed, "raw", identity=IDENTITY, symbol=SPEC.quote_code)
    with pytest.raises(DataError, match="unfinished"):
        parse_valuation_history(
            {"valuation_type": 1, "trend": {"historical_items": [valuation()]}},
            SPEC,
            START,
            END,
            observed,
            "raw",
            metric="pe",
            symbol=SPEC.quote_code,
        )


@pytest.mark.parametrize(
    "identity",
    [
        replace_identity
        for replace_identity in [
            {**IDENTITY, "code": "US..NDX"},
            {**IDENTITY, "stock_type": "ETF"},
            {**IDENTITY, "exchange": "HK"},
            {**IDENTITY, "name": ""},
        ]
    ],
)
def test_wrong_index_or_etf_identity_stops_requests(tmp_path, identity):
    with pytest.raises(DataError, match="identity"):
        run(tmp_path, identities=[identity])


def test_unlisted_instrument_is_not_permission_denial(tmp_path):
    with pytest.raises(FutuRestSourceError, match="instrument_not_listed"):
        run(tmp_path, identities=[])


def test_yearly_windows_continue_after_has_more_false_and_do_not_overlap(tmp_path):
    requests = []

    def history(request):
        requests.append((request.url.params["start"], request.url.params["end"]))
        if len(requests) == 1:
            return response(
                {"kline_list": [bar(date(2019, 12, 30)), bar(date(2019, 12, 31))]},
                pagination={"has_more": False},
            )
        return response(
            {"kline_list": [bar(date(2020, 1, 2)), bar(date(2020, 1, 3))]},
            pagination={"has_more": False},
        )

    result, _ = run(tmp_path, history=history, start=date(2019, 12, 30), end=date(2020, 1, 3))
    assert requests == [("2019-12-30", "2019-12-31"), ("2020-01-01", "2020-01-03")]
    assert {row.trade_date for row in result.observations} == {
        date(2019, 12, 30),
        date(2019, 12, 31),
        date(2020, 1, 2),
        date(2020, 1, 3),
    }
    assert not result.failures


def test_truncated_small_window_is_reported_without_discarding_valid_rows(tmp_path):
    def history(_request):
        return response(
            {"kline_list": [bar()]},
            pagination={"has_more": True, "next_time": int(timestamp(END).timestamp() * 1000)},
        )

    result, _ = run(tmp_path, history=history)
    assert len(result.observations) == 1 and len(result.valuations) == 3
    assert len(result.failures) == 1
    assert "truncated" in result.failures[0]["error"]


def test_ignored_date_boundaries_are_not_silently_accepted(tmp_path):
    def history(_request):
        return response(
            {"kline_list": [bar(START - timedelta(days=1)), bar()]},
            pagination={"has_more": False},
        )

    result, _ = run(tmp_path, history=history)
    assert not result.observations
    assert "outside" in result.failures[0]["error"]


def test_pre_epoch_request_limitation_does_not_block_later_history(tmp_path):
    requests = []

    def history(request):
        requests.append(request.url.params["start"])
        return response({"kline_list": [bar(date(1970, 1, 2))]}, pagination={"has_more": False})

    result, _ = run(tmp_path, history=history, start=date(1957, 3, 4), end=date(1970, 1, 2))
    assert requests == ["1970-01-01"]
    assert len(result.observations) == 1
    assert result.failures[0]["category"] == "unsupported_date_range_before_1970"
    assert result.failures[0]["end"] == date(1969, 12, 31)


def test_empty_early_year_is_a_coverage_gap_and_does_not_stop_later_years(tmp_path):
    requests = []

    def history(request):
        requests.append(request.url.params["start"])
        rows = [] if request.url.params["start"] < "1990" else [bar(date(1990, 1, 2))]
        return response({"kline_list": rows}, pagination={"has_more": False})

    result, _ = run(tmp_path, history=history, start=date(1985, 1, 31), end=date(1990, 1, 2))
    assert len(requests) == 6
    assert result.observations[0].trade_date == date(1990, 1, 2)
    assert not result.failures


def test_a_failed_year_keeps_prior_data_and_continues_later_years(tmp_path):
    def history(request):
        year = int(request.url.params["start"][:4])
        if year == 2019:
            return httpx.Response(503, json={"ret_code": -1})
        return response({"kline_list": [bar(date(year, 1, 2))]}, pagination={"has_more": False})

    result, _ = run(tmp_path, history=history, start=date(2018, 1, 1), end=date(2020, 1, 2))
    assert {row.trade_date.year for row in result.observations} == {2018, 2020}
    assert result.failures[0]["start"] == date(2019, 1, 1)
    assert result.failures[0]["category"] == "temporary_failure"


@pytest.mark.parametrize("http_status", [200, 429])
def test_rate_limit_waits_then_retries_the_identical_year_and_archives_both(
    tmp_path, request_waits, http_status
):
    requests = []

    def history(request):
        requests.append(str(request.url))
        if len(requests) == 1:
            return httpx.Response(
                http_status,
                headers={"Retry-After": "3"},
                json={
                    "ret_code": -11,
                    "ret_msg": "rate limit exceeded",
                    "error": {
                        "code": "rate_limited",
                        "message": "too many requests, retry after 8s",
                    },
                },
            )
        return response({"kline_list": [bar()]}, pagination={"has_more": False})

    result, _ = run(tmp_path, history=history)
    assert requests[0] == requests[1] and len(requests) == 2
    assert len(result.observations) == 1 and not result.failures
    assert any(delay == pytest.approx(8, abs=0.05) for delay in request_waits)
    assert all(0 <= delay <= 60 for delay in request_waits)
    archives = [json.loads(path.read_text()) for path in tmp_path.rglob("*.json")]
    assert len(archives) == 6
    assert sum(item["response"]["ret_code"] == -11 for item in archives) == 1


def test_rate_limit_exhaustion_is_bounded_and_preserves_the_failure(tmp_path, request_waits):
    requests = []

    def history(request):
        requests.append(str(request.url))
        return httpx.Response(429, headers={"Retry-After": "2"}, json={"ret_code": -11})

    result, _ = run(tmp_path, history=history)
    assert len(requests) == 4 and len(set(requests)) == 1
    assert not result.observations and len(result.valuations) == 3
    failure = result.failures[0]
    assert failure["category"] == "rate_limited" and failure["retry_after"] == 2
    assert Path(failure["artifact_path"]).exists()


def test_long_server_cooldown_is_reported_without_retrying_early(tmp_path, request_waits):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(429, headers={"Retry-After": "120"}, json={"ret_code": -11})

    with httpx.Client(
        base_url="https://example.test", transport=httpx.MockTransport(handler)
    ) as client:
        with pytest.raises(FutuRestSourceError) as caught:
            FutuRestHistory(Archive(tmp_path), client)._request("GET", "/quote")
    assert len(calls) == 1 and not request_waits
    assert caught.value.category == "rate_limited" and caught.value.retry_after == 120


def test_missing_retry_hint_uses_bounded_backoff(tmp_path, request_waits):
    attempts = []

    def history(request):
        attempts.append(request)
        if len(attempts) < 3:
            return httpx.Response(429, json={"ret_code": -11})
        return response({"kline_list": [bar()]}, pagination={"has_more": False})

    result, _ = run(tmp_path, history=history)
    assert not result.failures
    assert any(delay == pytest.approx(2, abs=0.05) for delay in request_waits)
    assert any(delay == pytest.approx(4, abs=0.05) for delay in request_waits)


def test_duplicate_days_in_one_page_are_not_admitted(tmp_path):
    result, _ = run(
        tmp_path,
        history=lambda _: response({"kline_list": [bar(), bar()]}, pagination={"has_more": False}),
    )
    assert not result.observations
    assert "duplicate" in result.failures[0]["error"]
    assert len(result.valuations) == 3


@pytest.mark.parametrize(
    "status,message,category",
    [
        (403, "无权限", "permission_denied"),
        (200, "US indices not supported", "unsupported_instrument"),
        (503, "unavailable", "temporary_failure"),
        (401, "missing token", "authentication_required"),
    ],
)
def test_metric_failure_keeps_price_and_other_metrics(tmp_path, status, message, category):
    def valuations(_request, metric):
        if metric == 1:
            return httpx.Response(status, json={"ret_code": 99, "ret_msg": message})
        return response({"valuation_type": metric, "trend": {"historical_items": [valuation()]}})

    result, _ = run(tmp_path, valuations=valuations)
    assert len(result.observations) == 1
    assert {row.metric for row in result.valuations} == {"pb", "ps"}
    assert result.failures[0]["category"] == category
    assert message not in result.failures[0]["error"]
    assert Path(result.failures[0]["artifact_path"]).exists()


def test_missing_valuation_is_not_replaced_by_kline_pe(tmp_path):
    result, _ = run(
        tmp_path,
        history=lambda _: response(
            {"kline_list": [bar(pe_ratio=55)]}, pagination={"has_more": False}
        ),
        valuations=lambda _, metric: response(
            {
                "valuation_type": metric,
                "trend": {"historical_items": [valuation(value=0)]},
            }
        ),
    )
    assert not result.valuations
    assert result.observations[0].pe_ttm is None


def test_snapshot_other_dates_and_negative_pe_are_preserved_without_fill():
    items = [
        valuation(START - timedelta(days=1)),
        valuation(END, value="-12.3"),
        valuation(END + timedelta(days=1)),
    ]
    result = parse_valuation_history(
        {"valuation_type": 1, "trend": {"historical_items": items}},
        SPEC,
        START,
        END,
        NOW,
        "raw",
        metric="pe",
        symbol=SPEC.quote_code,
    )
    assert len(result) == 1 and result[0].value == Decimal("-12.3")


@pytest.mark.parametrize("mutation", ["type", "time", "duplicate"])
def test_valuation_metric_and_dates_are_verified(mutation):
    item = valuation()
    data = {"valuation_type": 1, "trend": {"historical_items": [item]}}
    if mutation == "type":
        data["valuation_type"] = 2
    elif mutation == "time":
        item["time"] -= 86400
    else:
        data["trend"]["historical_items"].append(item.copy())
    with pytest.raises(DataError):
        parse_valuation_history(
            data, SPEC, START, END, NOW, "raw", metric="pe", symbol=SPEC.quote_code
        )


def test_missing_mapping_does_not_call_authenticated_client(tmp_path):
    class Client:
        def request(self, *_args, **_kwargs):
            pytest.fail("Missing mapping must be rejected before a request")

    with pytest.raises(DataError, match="configured"):
        FutuRestHistory(Archive(tmp_path), Client()).index_history(
            replace(SPEC, futorest_symbol=None),
            START,
            END,
        )
