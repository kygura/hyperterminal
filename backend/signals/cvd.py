"""
CVD Divergence Signal — detects when Cumulative Volume Delta diverges from price.

Price up + CVD declining → sellers absorbing → SHORT_BIAS (distribution).
Price down + CVD rising → buyers absorbing → LONG_BIAS (accumulation).
"""

from __future__ import annotations

import time
from typing import Optional

from data.store import DataStore
from signals.base import BaseSignal, SignalResult


class CVDDivergenceSignal(BaseSignal):
    """Detects divergence between price action and cumulative volume delta."""

    def evaluate(self, coin: str) -> Optional[SignalResult]:
        lookback_minutes = self.config.get("lookback_minutes", 30)
        price_change_threshold_pct = self.config.get("price_change_threshold_pct", 0.5)
        cvd_reversal_threshold_pct = self.config.get("cvd_reversal_threshold_pct", -20.0)
        thresholds = self.config.get("thresholds", {"low": 1.0, "medium": 1.5, "high": 2.0})
        min_trades = self.config.get("min_trades", 100)

        lookback_ms = int(lookback_minutes * 60 * 1000)
        trades = self.store.get_trades_window(coin, lookback_ms)

        if len(trades) < min_trades:
            self.logger.debug(
                "%s: only %d trades (need %d)", coin, len(trades), min_trades
            )
            return None

        # Price change: first to last trade
        price_start = trades[0]["px"]
        price_end = trades[-1]["px"]
        if price_start == 0:
            return None

        price_pct_change = ((price_end - price_start) / price_start) * 100
        price_up = price_pct_change >= 0

        # Significance filter
        if abs(price_pct_change) < price_change_threshold_pct:
            self.logger.debug(
                "%s: price change %.2f%% below threshold %.2f%%",
                coin, price_pct_change, price_change_threshold_pct,
            )
            return None

        # Cumulative CVD: split into first half and second half
        mid = len(trades) // 2
        first_half = trades[:mid]
        second_half = trades[mid:]

        def _cvd(tlist: list) -> float:
            total = 0.0
            for t in tlist:
                notional = t["sz"] * t["px"]
                total += notional if t["side"] == "B" else -notional
            return total

        cvd_first = _cvd(first_half)
        cvd_second = _cvd(second_half)
        cvd_total = cvd_first + cvd_second

        # CVD trend: second half vs first half
        if cvd_first == 0:
            cvd_trend_pct = 0.0
        else:
            cvd_trend_pct = ((cvd_second - cvd_first) / abs(cvd_first)) * 100

        # Divergence detection
        # Price up but CVD declining (buyers not driving the move)
        price_cvd_diverge = (price_up and cvd_trend_pct <= cvd_reversal_threshold_pct) or \
                            (not price_up and cvd_trend_pct >= abs(cvd_reversal_threshold_pct))

        if not price_cvd_diverge:
            self.logger.debug(
                "%s: no CVD divergence — price_pct=%.2f cvd_trend=%.2f%%",
                coin, price_pct_change, cvd_trend_pct,
            )
            return None

        direction = "SHORT_BIAS" if price_up else "LONG_BIAS"

        # Strength: normalize divergence magnitude
        div_magnitude = abs(cvd_trend_pct)
        high_thresh = thresholds.get("high", 2.0)
        score = div_magnitude / abs(cvd_reversal_threshold_pct)
        priority = self._map_to_priority(score, thresholds)
        if priority is None:
            return None
        strength = min(score / (high_thresh * 1.5), 1.0)

        cvd_sign = "+" if cvd_total >= 0 else ""
        cvd_units = f"{cvd_total / 1_000_000:.2f}M" if abs(cvd_total) > 1_000_000 else f"{cvd_total:.0f}"
        cvd_direction_str = "declining" if cvd_trend_pct < 0 else "rising"
        interpretation = "Buyers not driving the move. Distribution likely." if direction == "SHORT_BIAS" \
            else "Sellers not driving the move. Accumulation likely."

        message = (
            f"🔄 CVD DIVERGENCE — {coin} {direction}\n"
            f"Price: {price_pct_change:+.2f}% | CVD: {cvd_sign}{cvd_units} ({cvd_direction_str})\n"
            f"{interpretation}"
        )

        self.logger.debug(
            "%s: SIGNAL %s price=%.2f%% cvd_trend=%.2f%%",
            coin, direction, price_pct_change, cvd_trend_pct,
        )

        return SignalResult(
            signal_name=self.name,
            coin=coin,
            direction=direction,
            strength=strength,
            priority=priority,
            message=message,
            timestamp=time.time(),
            metadata={
                "price_pct_change": price_pct_change,
                "cvd_total": cvd_total,
                "cvd_trend_pct": cvd_trend_pct,
                "trade_count": len(trades),
            },
        )
