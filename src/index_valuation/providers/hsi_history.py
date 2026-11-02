"""Explicit official price-index mappings; never use gross/net return levels for valuation."""

from index_valuation.providers.hsi_history_base import HSIHistory, HSIProfile, parse_index_history

MAPPINGS = {"02083.00": ("HK.800700", "Hang Seng TECH Index")}
PROFILE = HSIProfile(
    mappings=MAPPINGS,
    code_field="hsi_symbol",
    kind="price",
    price_basis="unadjusted_close",
    stage="prices",
    archive_name="hsi_price_history",
    provider_codes={"02083.00": "HSTECH"},
)


def parse_history(payload, spec, start, end, observed_at, path):
    return parse_index_history(payload, spec, start, end, observed_at, path, PROFILE)


class HSIPriceHistory(HSIHistory):
    profile = PROFILE
