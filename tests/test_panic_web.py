import csv
from dataclasses import replace
from datetime import date
from decimal import Decimal
from io import StringIO

from fastapi.testclient import TestClient

from index_valuation.domain import IndexSpec
from index_valuation.panic_lines import enrich_panic_lines
from index_valuation.webapp import create_app

SPEC = IndexSpec("test", "Test", "000300", "SH.000300", band_min_observations=2)


class StoredSeries:
    def __init__(self):
        self.rows = enrich_panic_lines(
            SPEC,
            [
                {
                    "trade_date": day,
                    "price": Decimal("3000"),
                    "pe": Decimal(pe),
                    "implied_eps": Decimal("100"),
                    "price_source": "futu",
                    "price_status": "pass",
                    "pe_comparison_status": "missing",
                    "pe_source": "futu",
                    "pe_basis": "futu_valuation_pe",
                    "phase": "published",
                }
                for day, pe in [
                    (date(2019, 1, 1), "10"),
                    (date(2020, 1, 1), "20"),
                    (date(2024, 1, 1), "30"),
                    (date(2025, 1, 1), "100"),
                ]
            ],
        )
        self.cutoffs = []

    def index_series(self, key, method):
        return self.rows

    def panic_lines(self, specs, as_of=None):
        self.cutoffs.append(as_of)
        eligible = [row for row in self.rows if not as_of or row["trade_date"] <= as_of]
        return {SPEC.key: eligible[-1]["panic_line"]} if eligible else {}

    def greed_lines(self, specs, as_of=None):
        eligible = [row for row in self.rows if not as_of or row["trade_date"] <= as_of]
        return {SPEC.key: eligible[-1]["greed_line"]} if eligible else {}

    def latest_index_points(self, specs):
        return {SPEC.key: self.rows[-1]}


def test_live_line_is_dated_window_independent_and_exported_with_full_precision():
    repo = StoredSeries()
    with TestClient(create_app(repository=repo, specs={SPEC.key: SPEC})) as client:
        lines = []
        for chart, history in ((1, 3), (5, 5), (0, 10)):
            data = client.get(
                f"/api/indices/test/dashboard?as_of=2024-01-02&chart_years={chart}"
                f"&history_years={history}"
            ).json()
            line = data["panic_line"]
            lines.append(line)
            assert line["as_of"] == "2024-01-01"
            assert Decimal(line["threshold_pe"]) == Decimal("11")
            assert line == data["selected"]["panic_line"]
            assert data["greed_line"] == data["selected"]["greed_line"]
            assert data["greed_line"]["threshold_pe"] == "19.0"
            assert data["greed_line"]["version"] == "rolling_pe_p90_v1"
            assert data["greed_line"]["quantile"] == "0.9"
            assert data["greed_line"] == data["greed_overview"][0]["line"]
            assert data["panic_overview"][0]["line"] == line
            assert data["panic_reference"]["record"] is None
        assert lines[0] == lines[1] == lines[2]
        assert repo.cutoffs == [date(2024, 1, 1)] * 3
        response = client.get("/api/indices/test/export.csv?as_of=2024-01-02&chart_years=0")
        rows = list(csv.DictReader(StringIO(response.text.lstrip("\ufeff"))))
        assert rows[-1]["panic_threshold_pe"] == lines[0]["threshold_pe"]
        assert rows[-1]["panic_reference_points"] == lines[0]["reference_points"]
        assert rows[-1]["panic_history_end"] == "2020-01-01"
        assert rows[-1]["panic_version"] == "rolling_pe_p10_v1"
        assert rows[-1]["greed_version"] == "rolling_pe_p90_v1"
        assert rows[-1]["greed_threshold_pe"] == "19.0"
        assert rows[-1]["greed_reference_points"] == "1900.0"
        assert rows[0]["greed_threshold_pe"] == ""
        assert rows[0]["panic_threshold_pe"] == ""


def test_updates_are_visible_without_restarting_api_and_history_does_not_move():
    repo = StoredSeries()
    with TestClient(create_app(repository=repo, specs={SPEC.key: SPEC})) as client:
        latest = client.get("/api/indices/test/dashboard").json()["panic_line"]
        assert Decimal(latest["threshold_pe"]) == Decimal("21")
        repo.rows = enrich_panic_lines(
            SPEC, [*repo.rows, {**repo.rows[-1], "trade_date": date(2026, 1, 1)}]
        )
        updated = client.get("/api/indices/test/dashboard").json()["panic_line"]
        assert updated["as_of"] == "2026-01-01"
        assert Decimal(updated["threshold_pe"]) == Decimal("37")
        historical = client.get("/api/indices/test/dashboard?as_of=2025-01-01").json()
        assert historical["panic_line"] == latest


def test_paused_or_unrebuilt_index_has_no_live_threshold():
    repo = StoredSeries()
    paused = replace(SPEC, collection_enabled=False, collection_disabled_reason="No source")
    with TestClient(create_app(repository=repo, specs={SPEC.key: paused})) as client:
        data = client.get("/api/indices/test/dashboard").json()
        assert data["panic_line"]["status"] == "collection_paused"
        assert data["panic_overview"][0]["line"]["status"] == "collection_paused"
        assert data["panic_line"].get("threshold_pe") is None
        assert data["greed_line"]["status"] == "collection_paused"
        assert data["greed_overview"][0]["line"]["status"] == "collection_paused"
        response = client.get("/api/indices/test/export.csv")
        rows = list(csv.DictReader(StringIO(response.text.lstrip("\ufeff"))))
        assert rows[-1]["panic_status"] == "collection_paused"
        assert rows[-1]["panic_threshold_pe"] == ""
        assert rows[-1]["panic_reference_points"] == ""
        assert rows[-1]["greed_threshold_pe"] == ""
        assert rows[-1]["greed_reference_points"] == ""
    repo.rows = [{k: v for k, v in row.items() if k != "panic_line"} for row in repo.rows]
    repo.panic_lines = lambda specs, as_of=None: {}
    with TestClient(create_app(repository=repo, specs={SPEC.key: SPEC})) as client:
        data = client.get("/api/indices/test/dashboard").json()
        assert data["panic_line"]["status"] == "not_calculated"
        assert data["panic_line"].get("threshold_pe") is None
