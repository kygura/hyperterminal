from __future__ import annotations

import time
from typing import Optional

from signals.base import BaseSignal, SignalResult


class LeverageFlushSignal(BaseSignal):
    """Detects post-cascade deleveraging once forced flow starts to fade."""

    def evaluate(self, coin: str) -> Optional[SignalResult]:
        lookback_minutes = int(self.config.get("lookback_minutes", 60))
        min_liquidation_events = int(self.config.get("min_liquidation_events", 6))
        oi_drop_threshold_pct = float(self.config.get("oi_drop_threshold_pct", 3.0))
        price_displacement_threshold_pct = float(self.config.get("price_displacement_threshold_pct", 0.75))
        liquidation_share_threshold = float(self.config.get("liquidation_share_threshold", 0.6))
        decline_confirmation_ratio = float(self.config.get("decline_confirmation_ratio", 0.8))
        thresholds = self.config.get("thresholds", {"low": 1.0, "medium": 1.5, "high": 2.0})

        lookback_ms = lookback_minutes * 60 * 1000
        liquidations = self.store.get_liquidations_window(coin, lookback_ms)
        if len(liquidations) < min_liquidation_events:
            return None

        oi_data = self.store.get_oi_change(coin, lookback_ms)
        oi_pct_change = oi_data.get("oi_pct_change", 0.0)
        if oi_pct_change > -oi_drop_threshold_pct:
            return None

        snapshots = self.store.get_snapshots_window(coin, lookback_ms)
        if len(snapshots) < 2:
            return None
        price_start = snapshots[0].get("mark_px", 0.0)
        price_end = snapshots[-1].get("mark_px", 0.0)
        if price_start <= 0:
            return None
        price_pct_change = ((price_end - price_start) / price_start) * 100

        mid_ts = liquidations[0]["ts"] + (liquidations[-1]["ts"] - liquidations[0]["ts"]) / 2
        early = [liq for liq in liquidations if liq["ts"] < mid_ts]
        late = [liq for liq in liquidations if liq["ts"] >= mid_ts]
        if not early or not late:
            return None

        early_total = sum(liq["notional"] for liq in early)
        late_total = sum(liq["notional"] for liq in late)
        if late_total > early_total * decline_confirmation_ratio:
            return None

        long_notional = sum(liq["notional"] for liq in liquidations if liq["side"] == "B")
        short_notional = sum(liq["notional"] for liq in liquidations if liq["side"] == "S")
        total_notional = long_notional + short_notional
        if total_notional <= 0:
            return None

        dominant_side = "B" if long_notional >= short_notional else "S"
        dominant_share = max(long_notional, short_notional) / total_notional
        if dominant_share < liquidation_share_threshold:
            return None

        if dominant_side == "B" and price_pct_change <= -price_displacement_threshold_pct:
            direction = "LONG_BIAS"
            sub_signal = "post_long_flush"
        elif dominant_side == "S" and price_pct_change >= price_displacement_threshold_pct:
            direction = "SHORT_BIAS"
            sub_signal = "post_short_flush"
        else:
            return None

        score = max(
            abs(oi_pct_change) / max(oi_drop_threshold_pct, 0.01),
            abs(price_pct_change) / max(price_displacement_threshold_pct, 0.01),
            dominant_share / max(liquidation_share_threshold, 0.01),
        )
        priority = self._map_to_priority(score, thresholds)
        if priority is None:
            return None

        message = (
            f"🧼 LEVERAGE FLUSH — {coin} {direction}\n"
            f"OI: {oi_pct_change:+.2f}% | Price: {price_pct_change:+.2f}% | liq fade: {late_total / max(early_total, 1):.2f}x\n"
            f"Forced deleveraging is easing after a one-sided flush."
        )

        return SignalResult(
            signal_name=self.name,
            coin=coin,
            direction=direction,
            strength=min(score / (thresholds.get("high", 2.0) * 1.5), 1.0),
            priority=priority,
            message=message,
            timestamp=time.time(),
            metadata={
                "sub_signal": sub_signal,
                "oi_pct_change": oi_pct_change,
                "price_pct_change": price_pct_change,
                "dominant_side": dominant_side,
                "dominant_share": dominant_share,
                "early_liquidation_notional": early_total,
                "late_liquidation_notional": late_total,
            },
        )
