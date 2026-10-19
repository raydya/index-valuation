"""Current valuation signals from each current method's latest persisted point."""

from datetime import UTC, datetime

from index_valuation.index_series import basis_note, method_for, valuation_profile
from index_valuation.panic_lines import (
    GREED_PROBABILITY,
    HISTORY_YEARS,
    PROBABILITY,
    SIGNAL_VERSION,
)

LABELS = {
    "panic": ("恐慌", "持续买入"),
    "normal": ("普通", "继续持有"),
    "greed": ("贪婪", "减仓离场"),
    "unknown": ("暂无判断", None),
}
REASONS = {
    "collection_paused": "采集已暂停，保留历史供查阅",
    "missing_data": "当前方法尚无数据",
    "missing_pe": "最新数据日缺少有效正 PE",
    "different_basis": "最新 PE 的来源或口径与当前方法不一致",
    "different_method": "最新 PE 不属于当前方法",
    "unusable_pe": "最新 PE 未满足当前方法的计算条件",
    "insufficient_history": "同口径有效 PE 尚未满足五年跨度及最少样本要求",
    "flat_distribution": "恐慌与贪婪阈值相同，无法区分估值区间",
    "not_calculated": "最新数据尚未计算双线，请重建当前方法",
}


def build_market_overview(specs, points):
    """Never substitute a past usable PE for a missing latest-day observation."""
    items = []
    for spec in specs.values():
        point = points.get(spec.key) or {}
        profile = valuation_profile(spec)
        detail = point.get("valuation_signal") or {}
        current = bool(
            detail.get("version") == SIGNAL_VERSION
            and detail.get("as_of") == point.get("trade_date")
            and detail.get("method") == method_for(spec)
            and detail.get("source") == profile["source"]
            and detail.get("basis") == profile["basis"]
            and detail.get("pe") == point.get("pe")
            and detail.get("minimum_history") == spec.band_min_observations
            and detail.get("history_years") == HISTORY_YEARS
            and detail.get("history_frequency") == profile["history_frequency"]
        )
        if current and detail.get("status") == "reference":
            current = bool(
                point.get("pe_usable", True)
                and point.get("pe_source") == profile["source"]
                and point.get("pe_basis") == profile["basis"]
                and point.get("phase") == "published"
                and (spec.inception_date is None or point["trade_date"] >= spec.inception_date)
            )
        status = detail.get("status", "not_calculated") if current else "not_calculated"
        if not spec.collection_enabled:
            status = "collection_paused"
        elif not point:
            status = "missing_data"
        signal = detail.get("signal", "unknown") if status == "reference" else "unknown"
        if signal not in LABELS:
            signal, status = "unknown", "not_calculated"
        label, action = LABELS[signal]
        reason = (
            spec.collection_disabled_reason
            if status == "collection_paused" and spec.collection_disabled_reason
            else REASONS.get(status)
        )
        if signal != "unknown":
            reason = {
                "panic": "最新 PE 不高于此前五年的 10% 分位",
                "normal": "最新 PE 位于此前五年的 10% 与 90% 分位之间",
                "greed": "最新 PE 不低于此前五年的 90% 分位",
            }[signal]
        visible = detail if current and spec.collection_enabled else {}
        items.append(
            {
                "key": spec.key,
                "name": spec.name,
                "market": spec.market,
                "as_of": point.get("trade_date"),
                "price": point.get("price"),
                "pe": point.get("pe"),
                "signal": signal,
                "label": label,
                "action": action,
                "reason": reason,
                "status": status,
                **{
                    key: visible.get(key)
                    for key in (
                        "lower_pe",
                        "upper_pe",
                        "history_start",
                        "history_end",
                        "history_count",
                        "history_complete",
                        "minimum_history",
                        "history_frequency",
                    )
                },
                "source": profile["source"],
                "basis": profile["basis"],
                "method": method_for(spec),
                "source_label": profile["label"],
                "basis_note": basis_note(spec),
                "calculated_at": point.get("calculated_at"),
            }
        )
    return {
        "checked_at": datetime.now(UTC),
        "rule": {
            "version": SIGNAL_VERSION,
            "history_years": HISTORY_YEARS,
            "lower_quantile": PROBABILITY,
            "upper_quantile": GREED_PROBABILITY,
            "description": (
                "以各指数当前方法的最新数据日为准：PE≤此前五年同口径有效 PE 的 P10 为恐慌，"
                "PE≥P90 为贪婪，其间为普通；排除当天、未来和发布前回溯。"
                "五年跨度及配置样本数不足、最新 PE 不可用、两阈值相同或采集暂停时不作判断。"
                "周度方法仅使用已结束自然周的最后有效值。"
            ),
        },
        "items": items,
    }
