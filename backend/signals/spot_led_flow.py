"""
Spot-Led Flow Signal — identifies organic accumulation or distribution.

Real money flow not driven by leverage. Highest-conviction directional signal.

Logic (v2 spec §3.3):
  - Spot volume surge: +20% vs 24h rolling average
  - OI condition: flat or declining (±3% band)
  - Direction follows prevailing price trend during the surge
  - Confirmation: 2 consecutive readings required
  - Standalone conviction: LOW-MEDIUM
"""

from __future__ import annotations

import time
from typing import Optional

from signals.base import BaseSignal, SignalResult


class SpotLedFlowSignal(BaseSignal):
    """Detects organic spot-driven accumulation or distribution."""

    def __init__(self, name: str, config: dict, store) -> None:
        super().__init__(name, config, store)
        # Track consecutive readings per coin for confirmation requirement
        self._consecutive: dict[str, int] = {}
        self._last_direction: dict[str, str] = {}

    def evaluate(self, coin: str) -> Optional[SignalResult]:
        surge_threshold_pct = self.config.get("spot_surge_threshold_pct", 20.0)
        oi_flat_band_pct = self.config.get("oi_flat_band_pct", 3.0)
        lookback_hours = self.config.get("lookback_hours", 24)
        confirm_readings = self.config.get("confirm_readings", 2)
        thresholds = self.config.get("thresholds", {"low": 1.0, "medium": 1.5, "high": 2.0})

        lookback_ms = int(lookback_hours * 3600 * 1000)
        short_ms = int(lookback_ms / 4)  # current window ≈ 6h

        # --- Volume: current vs rolling avg ---
        cur_vol = self.store.get_volume_summary(coin, short_ms)
        avg_vol_per_bucket = self.store.get_spot_volume_rolling_avg(coin, lookback_ms)

        # For stores that don't have get_spot_volume_rolling_avg (in-memory store),
        # fall back to total_volume ratio
        if hasattr(self.store, 'get_spot_volume_rolling_avg'):
            rolling_avg = avg_vol_per_bucket
        else:
            prior_vol = self.store.get_volume_summary(coin, lookback_ms)
            rolling_avg = prior_vol["total_volume"] / max(lookback_hours, 1)

        # Estimate current hourly volume
        current_hourly = (cur_vol["spot_volume"] or cur_vol["total_volume"])
        if current_hourly == 0 or rolling_avg == 0:
            self._consecutive[coin] = 0
            self.logger.debug("%s: no volume data for spot-led check", coin)
            return None

        vol_surge_pct = ((current_hourly - rolling_avg) / rolling_avg) * 100

        # --- OI: check flatness ---
        oi_data = self.store.get_oi_change(coin, short_ms)
        oi_pct = oi_data.get("oi_pct_change", 0.0)

        # Check conditions
        is_spot_surge = vol_surge_pct >= surge_threshold_pct
        is_oi_flat = abs(oi_pct) <= oi_flat_band_pct

        if not (is_spot_surge and is_oi_flat):
            self._consecutive[coin] = 0
            self.logger.debug(
                "%s: spot-led conditions not met — vol_surge=%.1f%% oi_pct=%.1f%%",
                coin, vol_surge_pct, oi_pct,
            )
            return None

        # --- Direction: price trend during surge ---
        snaps = self.store.get_snapshots_window(coin, short_ms)
        if len(snaps) >= 2:
            price_start = snaps[0].get("mark_px", 0)
            price_end = snaps[-1].get("mark_px", 0)
            price_up = price_end >= price_start
        else:
            # Fall back to OI direction (price unavailable)
            price_up = True

        direction = "LONG_BIAS" if price_up else "SHORT_BIAS"

        # --- Confirmation: require N consecutive matching readings ---
        if self._last_direction.get(coin) != direction:
            self._consecutive[coin] = 1
            self._last_direction[coin] = direction
        else:
            self._consecutive[coin] = self._consecutive.get(coin, 0) + 1

        if self._consecutive[coin] < confirm_readings:
            self.logger.debug(
                "%s: spot-led building confirmation %d/%d — %s vol_surge=%.1f%%",
                coin, self._consecutive[coin], confirm_readings, direction, vol_surge_pct,
            )
            return None

        # --- Strength and priority ---
        surge_score = vol_surge_pct / surge_threshold_pct  # 1.0 at threshold, >1.0 above
        priority = self._map_to_priority(surge_score, thresholds)
        if priority is None:
            return None

        high_thresh = thresholds.get("high", 2.0)
        strength = min(surge_score / (high_thresh * 1.5), 1.0)

        direction_word = "ACCUMULATION" if price_up else "DISTRIBUTION"
        oi_desc = f"{'flat' if abs(oi_pct) < 1.5 else ('declining' if oi_pct < 0 else 'rising')} ({oi_pct:+.1f}%)"
        message = (
            f"🌊 SPOT-LED FLOW — {coin} {direction}\n"
            f"Spot vol surge: +{vol_surge_pct:.0f}% vs 24h avg\n"
            f"OI: {oi_desc} — organic {direction_word.lower()}."
        )

        self.logger.debug(
            "%s: SIGNAL %s vol_surge=%.1f%% oi=%.1f%% strength=%.2f",
            coin, direction, vol_surge_pct, oi_pct, strength,
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
                "vol_surge_pct": vol_surge_pct,
                "oi_pct": oi_pct,
                "price_up": price_up,
                "consecutive_confirmations": self._consecutive[coin],
                "sub_signal": "spot_led_flow",
            },
        )
