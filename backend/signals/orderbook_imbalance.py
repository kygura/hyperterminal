from __future__ import annotations

import statistics
import time
from typing import Optional

from signals.base import BaseSignal, SignalResult


class OrderbookImbalanceSignal(BaseSignal):
    """Detects persistent directional pressure in the top-of-book depth."""

    def evaluate(self, coin: str) -> Optional[SignalResult]:
        lookback_minutes = self._resolve_minutes()
        min_snapshots = self._resolve_count("min_snapshots", default=20)
        persistence_count = self._resolve_count("persistence_count", default=5)
        imbalance_threshold = float(self.config.get("imbalance_threshold", 0.15))
        thresholds = self.config.get("thresholds", {"low": 1.0, "medium": 1.5, "high": 2.0})

        snapshots = self.store.get_orderbook_imbalance_window(coin, lookback_minutes * 60 * 1000)
        if len(snapshots) < min_snapshots:
            self.logger.debug("%s: only %d book snapshots (need %d)", coin, len(snapshots), min_snapshots)
            return None

        ratios = [row["imbalance_ratio"] for row in snapshots]
        latest_ratio = ratios[-1]
        directional_imbalance = latest_ratio - 0.5
        if abs(directional_imbalance) < imbalance_threshold:
            self.logger.debug("%s: imbalance %.3f below threshold %.3f", coin, directional_imbalance, imbalance_threshold)
            return None

        same_side_count = 0
        for row in reversed(snapshots):
            row_imbalance = row["imbalance_ratio"] - 0.5
            if directional_imbalance > 0 and row_imbalance >= imbalance_threshold:
                same_side_count += 1
            elif directional_imbalance < 0 and row_imbalance <= -imbalance_threshold:
                same_side_count += 1
            else:
                break

        if same_side_count < persistence_count:
            self.logger.debug(
                "%s: persistence %d/%d below threshold",
                coin,
                same_side_count,
                persistence_count,
            )
            return None

        z_score = 0.0
        if len(ratios) >= 2:
            std = statistics.stdev(ratios)
            if std > 0:
                z_score = (latest_ratio - statistics.mean(ratios)) / std

        score = max(abs(z_score), abs(directional_imbalance) / imbalance_threshold)
        priority = self._map_to_priority(score, thresholds)
        if priority is None:
            return None

        direction = "LONG_BIAS" if directional_imbalance > 0 else "SHORT_BIAS"
        latest = snapshots[-1]
        strength = min(score / (thresholds.get("high", 2.0) * 1.5), 1.0)
        message = (
            f"📚 ORDERBOOK IMBALANCE — {coin} {direction}\n"
            f"Book ratio: {latest_ratio:.2f} | Persistence: {same_side_count} snaps\n"
            f"Top-of-book depth is skewed {'bid-heavy' if direction == 'LONG_BIAS' else 'ask-heavy'}."
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
                "imbalance_ratio": latest_ratio,
                "imbalance_delta": directional_imbalance,
                "z_score": z_score,
                "bid_total": latest["bid_total"],
                "ask_total": latest["ask_total"],
                "spread": latest["spread"],
                "mid_px": latest["mid_px"],
                "persistence_count": same_side_count,
            },
        )

    def _resolve_minutes(self) -> int:
        timeframe = getattr(self, "global_config", {}).get("strategy", {}).get("timeframe", "hourly")
        base_minutes = int(self.config.get("lookback_minutes", 60))
        if timeframe == "daily":
            return int(self.config.get("daily_lookback_minutes", base_minutes * 24))
        return base_minutes

    def _resolve_count(self, key: str, default: int) -> int:
        timeframe = getattr(self, "global_config", {}).get("strategy", {}).get("timeframe", "hourly")
        value = int(self.config.get(key, default))
        if timeframe == "daily":
            return int(self.config.get(f"daily_{key}", max(value * 3, value)))
        return value
