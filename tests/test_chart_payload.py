"""The browser's lean series must preserve values and the full audit/export path."""

import csv
from datetime import date
from decimal import Decimal
from io import StringIO

import pytest
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
                    "price": Decimal("3000.1234567890123456789"),
                    "pe": Decimal(pe) if pe else None,
                    "implied_eps": Decimal("100.1234567890123456789") if pe else None,
                    "pb": Decimal("1.23"),
                    "ps": None,
                    "reference_pe": Decimal("15"),
                    "price_source": "futu",
                    "price_status": "unverified",
                    "pe_comparison_status": "warning",
                    "pe_source": "futu",
                    "pe_basis": "futu_valuation_pe",
                    "pe_usable": True,
                    "phase": "published",
                }
                for day, pe in (
                    (date(2019, 1, 1), "10"),
                    (date(2020, 1, 1), "20"),
                    (date(2024, 1, 1), None),
                    (date(2024, 1, 2), "30.1234567890123456789"),
                )
            ],
        )

    def index_series(self, key, method):
        return self.rows

    def panic_lines(self, specs, as_of=None):
        return {}

    def greed_lines(self, specs, as_of=None):
        return {}


@pytest.mark.parametrize(
    "parameters",
    [
        {},
        {"chart_years": 0, "history_years": 0},
        {"as_of": "2024-01-01", "chart_years": 1, "history_years": 3},
        {"chart_years": 1, "history_years": 10},
    ],
)
def test_chart_view_preserves_all_browser_values_and_full_exports(parameters):
    repo = StoredSeries()
    with TestClient(create_app(repository=repo, specs={SPEC.key: SPEC})) as client:
        full_response = client.get("/api/indices/test/dashboard", params=parameters)
        full = full_response.json()
        before_csv = client.get("/api/indices/test/export.csv", params=parameters).content
        response = client.get("/api/indices/test/dashboard", params={**parameters, "view": "chart"})
        assert response.status_code == 200
        chart = response.json()
        assert {k: v for k, v in chart.items() if k != "series"} == {
            k: v for k, v in full.items() if k != "series"
        }
        assert len(chart["series"]) == len(full["series"])
        for lean, complete in zip(chart["series"], full["series"], strict=True):
            # Needed across all chart modes, tooltip, and the paginated history table.
            assert set(lean) == {
                "trade_date",
                "price",
                "pe",
                "implied_eps",
                "pb",
                "ps",
                "reference_pe",
                "price_source",
                "price_status",
                "pe_comparison_status",
                "phase",
                "percentile",
                "mean_pe",
                "mean_points",
                "fixed",
                "quantile_pe",
                "quantile_points",
            }
            assert lean == {key: complete[key] for key in lean}
            assert "panic_line" in complete and "valuation_signal" in complete
            assert "total_return_basis" in complete
            assert lean["price"] == "3000.1234567890123456789"
            if lean["pe"] is None:
                assert lean["implied_eps"] is None and lean["fixed"] == {}
            else:
                assert "8" in lean["fixed"]
        assert b"\n" not in response.content
        assert len(response.content) < len(full_response.content)
        assert client.get("/api/indices/test/export.csv", params=parameters).content == before_csv
        exported = list(csv.DictReader(StringIO(before_csv.decode("utf-8-sig"))))
        assert "panic_version" in exported[-1] and "total_return_basis" in exported[-1]
        assert exported[-1]["price"] == chart["selected"]["price"]
        assert (
            client.get("/api/indices/test/dashboard", params={**parameters, "view": "full"}).json()
            == full
        )


def test_unknown_dashboard_view_is_rejected():
    with TestClient(create_app(repository=StoredSeries(), specs={SPEC.key: SPEC})) as client:
        assert client.get("/api/indices/test/dashboard?view=unsupported").status_code == 422
