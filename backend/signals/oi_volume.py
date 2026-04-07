"""
OI / Volume Divergence Signal — two sub-signals in one class.

Fade signal: Rising OI + declining volume → unsupported move, expect reversal.
Continuation signal: Rising volume + flat/declining OI → organic, spot-driven move.
"""

from __future__ import annotations

import time
from typing import Optional

from data.store import DataStore
from signals.base import BaseSignal, SignalResult


class OIVolumeDivergenceSignal(BaseSignal):
    """Detects divergence between open interest changes and trading volume."""

    def evaluate(self, coin: str) -> Optional[SignalResult]:
        lookback_minutes = self.config.get("lookback_minutes", 60)
        oi_threshold_pct = self.config.get("oi_change_threshold_pct", 2.0)
        vol_decline_thresh = self.config.get("volume_decline_threshold_pct", -15.0)
        thresholds = self.config.get("thresholds", {"low": 1.0, "medium": 1.5, "high": 2.0})
        min_snapshots = self.config.get("min_snapshots", 6)

        lookback_ms = int(lookback_minutes * 60 * 1000)
        half_ms = lookback_ms // 2

        # --- Current window ---
        cur_snaps = self.store.get_snapshots_window(coin, lookback_ms)
        if len(cur_snaps) < min_snapshots:
            self.logger.debug("%s: not enough snapshots (%d)", coin, len(cur_snaps))
            return None

        cur_vol = self.store.get_volume_summary(coin, lookback_ms)
        oi_data = self.store.get_oi_change(coin, lookback_ms)

        # --- Prior window (offset by one lookback period) ---
        prior_vol = self.store.get_volume_summary(coin, lookback_ms * 2)
        # prior volume = 2x window minus current window (approximate)
        prior_total = max(prior_vol["total_volume"] - cur_vol["total_volume"], 0.0)

        cur_total = cur_vol["total_volume"]
        if cur_total == 0 or prior_total == 0:
            self.logger.debug("%s: zero volume data", coin)
            return None

        vol_pct_change = ((cur_total - prior_total) / prior_total) * 100
        oi_pct = oi_data["oi_pct_change"]

        # Price trend from snapshots
        price_start = cur_snaps[0]["mark_px"]
        price_end = cur_snaps[-1]["mark_px"]
        price_up = price_end >= price_start

        # --- Fade signal: rising OI + declining volume ---
        if oi_pct >= oi_threshold_pct and vol_pct_change <= vol_decline_thresh:
            direction = "SHORT_BIAS" if price_up else "LONG_BIAS"
            divergence_score = abs(oi_pct) * abs(vol_pct_change) / 100
            priority = self._map_to_priority(divergence_score, thresholds)
            if priority is None:
                return None
            strength = min(divergence_score / (thresholds.get("high", 2.0) * 2), 1.0)
            move_type = "unsupported rally" if price_up else "unsupported selloff"
            message = (
                f"📊 OI/VOL DIVERGENCE — {coin} {direction}\n"
                f"OI: +{oi_pct:.1f}% | Volume: {vol_pct_change:.0f}% vs prior hour\n"
                f"Rising OI on declining volume. {move_type.capitalize()}. Move lacks conviction."
            )
            self.logger.debug("%s: FADE signal %s oi=%.2f%% vol=%.2f%%", coin, direction, oi_pct, vol_pct_change)
            return SignalResult(
                signal_name=self.name,
                coin=coin,
                direction=direction,
                strength=strength,
                priority=priority,
                message=message,
                timestamp=time.time(),
                metadata={
                    "sub_signal": "fade",
                    "oi_pct": oi_pct,
                    "vol_pct_change": vol_pct_change,
                    "price_up": price_up,
                },
            )

        # --- Continuation signal: rising volume + flat/declining OI ---
        vol_rising_thresh = 15.0  # volume up at least 15%
        oi_flat_max = 1.0          # OI change within ±1%
        if vol_pct_change >= vol_rising_thresh and abs(oi_pct) <= oi_flat_max:
            direction = "LONG_BIAS" if price_up else "SHORT_BIAS"
            vol_score = vol_pct_change / 10  # scale to multi
            priority = self._map_to_priority(vol_score, thresholds)
            if priority is None:
                return None
            strength = min(vol_score / (thresholds.get("high", 2.0) * 5), 1.0)
            message = (
                f"📈 ORGANIC FLOW — {coin} {direction}\n"
                f"Volume: +{vol_pct_change:.0f}% | OI: {oi_pct:+.1f}%\n"
                f"Spot-driven move. Trend continuation."
            )
            self.logger.debug("%s: CONTINUATION signal %s vol=%.2f%% oi=%.2f%%", coin, direction, vol_pct_change, oi_pct)
            return SignalResult(
                signal_name=self.name,
                coin=coin,
                direction=direction,
                strength=strength,
                priority=priority,
                message=message,
                timestamp=time.time(),
                metadata={
                    "sub_signal": "continuation",
                    "oi_pct": oi_pct,
                    "vol_pct_change": vol_pct_change,
                    "price_up": price_up,
                },
            )

        self.logger.debug(
            "%s: no OI/vol signal — oi=%.2f%% vol=%.2f%%", coin, oi_pct, vol_pct_change
        )
        return None
