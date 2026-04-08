from __future__ import annotations

import time
from typing import Optional

from signals.base import BaseSignal, SignalResult


class FundingVelocitySignal(BaseSignal):
    """Detects accelerating funding crowding before static extremes fully register."""

    def evaluate(self, coin: str) -> Optional[SignalResult]:
        lookback_hours = int(self.config.get("lookback_hours", 24))
        min_samples = int(self.config.get("min_samples", 8))
        velocity_window = int(self.config.get("velocity_window", 3))
        velocity_threshold = float(self.config.get("velocity_threshold", 0.00002))
        acceleration_threshold = float(self.config.get("acceleration_threshold", 0.00001))
        price_divergence_threshold_pct = float(self.config.get("price_divergence_threshold_pct", 0.25))
        thresholds = self.config.get("thresholds", {"low": 1.0, "medium": 1.5, "high": 2.0})

        entries = self.store.get_funding_window(coin, lookback_hours * 3600 * 1000)
        if len(entries) < max(min_samples, velocity_window * 2 + 1):
            self.logger.debug("%s: insufficient funding samples for velocity", coin)
            return None

        rates = [entry["rate"] for entry in entries]
        deltas = [curr - prev for prev, curr in zip(rates[:-1], rates[1:])]
        if len(deltas) < velocity_window * 2:
            return None

        recent_velocity = sum(deltas[-velocity_window:]) / velocity_window
        prior_velocity = sum(deltas[-velocity_window * 2:-velocity_window]) / velocity_window
        acceleration = recent_velocity - prior_velocity
        latest_rate = rates[-1]

        if latest_rate > 0 and recent_velocity > velocity_threshold and acceleration > acceleration_threshold:
            direction = "SHORT_BIAS"
            crowd = "long"
        elif latest_rate < 0 and recent_velocity < -velocity_threshold and acceleration < -acceleration_threshold:
            direction = "LONG_BIAS"
            crowd = "short"
        else:
            return None

        price_pct_change = self._price_change_pct(coin, lookback_hours * 3600 * 1000)
        price_divergence = (
            direction == "SHORT_BIAS" and price_pct_change <= price_divergence_threshold_pct
        ) or (
            direction == "LONG_BIAS" and price_pct_change >= -price_divergence_threshold_pct
        )

        score = max(
            abs(recent_velocity) / max(velocity_threshold, 1e-9),
            abs(acceleration) / max(acceleration_threshold, 1e-9),
        )
        if price_divergence:
            score += 0.5

        priority = self._map_to_priority(score, thresholds)
        if priority is None:
            return None

        divergence_note = " Early unwind warning." if price_divergence else ""
        message = (
            f"🏎 FUNDING VELOCITY — {coin} {direction}\n"
            f"Funding: {latest_rate:+.4%} | velocity: {recent_velocity:+.5f} | accel: {acceleration:+.5f}\n"
            f"{crowd.capitalize()} crowding is intensifying.{divergence_note}"
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
                "latest_rate": latest_rate,
                "recent_velocity": recent_velocity,
                "prior_velocity": prior_velocity,
                "acceleration": acceleration,
                "price_pct_change": price_pct_change,
                "price_divergence": price_divergence,
                "sub_signal": "funding_velocity",
            },
        )

    def _price_change_pct(self, coin: str, lookback_ms: int) -> float:
        snapshots = self.store.get_snapshots_window(coin, lookback_ms)
        if len(snapshots) < 2:
            return 0.0
        price_start = snapshots[0].get("mark_px", 0.0)
        price_end = snapshots[-1].get("mark_px", 0.0)
        if price_start <= 0:
            return 0.0
        return ((price_end - price_start) / price_start) * 100
