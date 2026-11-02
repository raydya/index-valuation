"""Hang Seng official gross-total-return history, distinct from price valuation."""

from index_valuation.providers.hsi_history_base import HSIHistory, HSIProfile, parse_index_history

MAPPINGS = {
    "12083.00": ("HK.HSTECHT", "Hang Seng TECH Index (Gross Total Return Index)"),
}
PROFILE = HSIProfile(
    mappings=MAPPINGS,
    code_field="provider_code",
    kind="total-return",
    price_basis="gross_total_return",
    stage="total_returns",
    archive_name="hsi_total_returns",
)


def parse_history(payload, spec, start, end, observed_at, path):
    return parse_index_history(payload, spec, start, end, observed_at, path, PROFILE)


class HSITotalReturnHistory(HSIHistory):
    profile = PROFILE
