"""
Premium Extremes Signal — perp vs spot (oracle) premium diverges from norm.

Positive premium (mark > oracle) → longs paying → SHORT_BIAS (reversion).
Negative premium → shorts paying → LONG_BIAS.

Also factors in premium velocity (rate of expansion/contraction).
"""

from __future__ import annotations

import statistics
import time
from typing import Optional

from data.store import DataStore
from signals.base import BaseSignal, SignalResult


class PremiumExtremesSignal(BaseSignal):
    """Detects when perp premium is statistically extreme or expanding rapidly."""

    def evaluate(self, coin: str) -> Optional[SignalResult]:
        lookback_minutes = self.config.get("lookback_minutes", 120)
        thresholds = self.config.get("thresholds", {"low": 0.15, "medium": 0.25, "high": 0.40})
        velocity_weight = self.config.get("premium_velocity_weight", 0.3)
        velocity_lookback_minutes = self.config.get("velocity_lookback_minutes", 15)
        min_snapshots = self.config.get("min_snapshots", 12)

        lookback_ms = int(lookback_minutes * 60 * 1000)
        velocity_ms = int(velocity_lookback_minutes * 60 * 1000)

        snaps = self.store.get_snapshots_window(coin, lookback_ms)

        if len(snaps) < min_snapshots:
            self.logger.debug(
                "%s: only %d snapshots (need %d)", coin, len(snaps), min_snapshots
            )
            return None

        # Premium series: (mark_px - oracle_px) / oracle_px * 100
        premiums = []
        for s in snaps:
            if s["oracle_px"] == 0:
                continue
            p = (s["mark_px"] - s["oracle_px"]) / s["oracle_px"] * 100
            premiums.append(p)

        if len(premiums) < min_snapshots:
            return None

        mean_p = statistics.mean(premiums)
        if len(premiums) > 1:
            std_p = statistics.stdev(premiums)
        else:
            std_p = 0.0

        latest_premium = premiums[-1]

        # Premium velocity: rate of change over velocity window
        velocity_snaps = self.store.get_snapshots_window(coin, velocity_ms)
        if len(velocity_snaps) >= 2 and velocity_snaps[0]["oracle_px"] != 0 and velocity_snaps[-1]["oracle_px"] != 0:
            vel_start = (velocity_snaps[0]["mark_px"] - velocity_snaps[0]["oracle_px"]) / velocity_snaps[0]["oracle_px"] * 100
            vel_end = (velocity_snaps[-1]["mark_px"] - velocity_snaps[-1]["oracle_px"]) / velocity_snaps[-1]["oracle_px"] * 100
            velocity = vel_end - vel_start  # pct change per velocity window
        else:
            velocity = 0.0

        # Combined score
        combined_score = (1 - velocity_weight) * abs(latest_premium) + velocity_weight * abs(velocity)

        priority = self._map_to_priority(combined_score, thresholds)
        if priority is None:
            self.logger.debug(
                "%s: combined score %.4f below threshold", coin, combined_score
            )
            return None

        direction = "SHORT_BIAS" if latest_premium > 0 else "LONG_BIAS"
        high_thresh = thresholds.get("high", 0.40)
        strength = min(combined_score / (high_thresh * 2), 1.0)

        if std_p > 0:
            z_score = (latest_premium - mean_p) / std_p
            z_str = f"z: {z_score:+.1f}σ"
        else:
            z_str = "z: n/a"

        if abs(velocity) > 0.01:
            if velocity > 0:
                vel_desc = "expanding rapidly" if velocity > 0.05 else "expanding"
            else:
                vel_desc = "contracting rapidly" if velocity < -0.05 else "contracting"
        else:
            vel_desc = "stable"

        reversion_context = "Perp above spot. Reversion likely." if direction == "SHORT_BIAS" \
            else "Perp below spot. Reversion likely."

        message = (
            f"💎 PREMIUM EXTREME — {coin} {direction}\n"
            f"Premium: {latest_premium:+.3f}% (mean: {mean_p:+.3f}%, {z_str})\n"
            f"Velocity: {vel_desc}\n"
            f"{reversion_context}"
        )

        self.logger.debug(
            "%s: SIGNAL %s premium=%.4f%% velocity=%.4f score=%.4f",
            coin, direction, latest_premium, velocity, combined_score,
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
                "premium_pct": latest_premium,
                "mean_premium": mean_p,
                "velocity": velocity,
                "combined_score": combined_score,
                "snapshots": len(snaps),
            },
        )
