"""
Funding Extremes Signal — detects statistically extreme funding rates.

Logic:
  1. Pull funding history from store within lookback window.
  2. Compute mean and std dev of rates.
  3. Z-score latest rate vs the distribution.
  4. Positive z-score (high funding, longs paying) → SHORT_BIAS.
  5. Negative z-score → LONG_BIAS.
"""

from __future__ import annotations

import math
import statistics
import time
from typing import Optional

from data.store import DataStore
from signals.base import BaseSignal, SignalResult


class FundingExtremesSignal(BaseSignal):
    """Detects when funding rate is statistically extreme vs recent history."""

    def evaluate(self, coin: str) -> Optional[SignalResult]:
        lookback_hours = self.config.get("lookback_hours", 24)
        min_samples = self.config.get("min_samples", 12)
        thresholds = self.config.get("thresholds", {"low": 1.5, "medium": 2.0, "high": 2.5})

        lookback_ms = int(lookback_hours * 3600 * 1000)
        entries = self.store.get_funding_window(coin, lookback_ms)

        if len(entries) < min_samples:
            self.logger.debug(
                "%s: only %d samples (need %d)", coin, len(entries), min_samples
            )
            return None

        rates = [e["rate"] for e in entries]
        mean = statistics.mean(rates)
        if len(rates) < 2:
            return None
        std = statistics.stdev(rates)

        if std == 0:
            self.logger.debug("%s: zero std dev in funding rates", coin)
            return None

        latest_rate = rates[-1]
        z_score = (latest_rate - mean) / std

        low_thresh = thresholds.get("low", 1.5)
        if abs(z_score) < low_thresh:
            self.logger.debug("%s: z=%.2f below threshold %.2f", coin, z_score, low_thresh)
            return None

        direction = "SHORT_BIAS" if z_score > 0 else "LONG_BIAS"

        priority = self._map_to_priority(abs(z_score), thresholds)
        if priority is None:
            return None

        high_thresh = thresholds.get("high", 2.5)
        strength = min(abs(z_score) / (high_thresh * 1.5), 1.0)

        sign = "+" if z_score > 0 else ""
        rate_pct = latest_rate * 100
        mean_pct = mean * 100
        crowd = "Longs heavily crowded. Fade candidate." if direction == "SHORT_BIAS" else "Shorts heavily crowded. Squeeze candidate."
        message = (
            f"⚡ FUNDING EXTREME — {coin} {direction}\n"
            f"Rate: {rate_pct:.4f}% (mean: {mean_pct:.4f}%, z: {sign}{z_score:.1f}σ)\n"
            f"{crowd}"
        )

        self.logger.debug(
            "%s: SIGNAL %s z=%.2f strength=%.2f priority=%s",
            coin, direction, z_score, strength, priority,
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
                "z_score": z_score,
                "mean_rate": mean,
                "latest_rate": latest_rate,
                "std": std,
                "samples": len(rates),
            },
        )
