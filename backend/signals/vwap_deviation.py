"""
VWAP Deviation Signal — contextual layer based on auction market theory.

Measures price displacement from fair value anchors.
Default mode: MODIFIER ONLY — amplifies or dampens other signals.
Can be enabled standalone via config.

Logic (v2 spec §3.4):
  - Session VWAP: computed from intraday OHLCV (daily reset at UTC midnight)
  - Deviation bands: ±1σ, ±2σ of rolling VWAP values
  - Price below VWAP → strengthens long signals (long modifier)
  - Price above VWAP → strengthens short signals (short modifier)
  - At VWAP (within band): signals dampened / fair value
"""

from __future__ import annotations

import time
from typing import Optional

from signals.base import BaseSignal, SignalResult


class VWAPDeviationSignal(BaseSignal):
    """
    VWAP contextual modifier.
    Emits a signal only when enabled_standalone=True in config (default: False).
    Always updates metadata for use by the confluence engine as a modifier.
    """

    def evaluate(self, coin: str) -> Optional[SignalResult]:
        standalone = self.config.get("enabled_standalone", False)
        lookback_ms = int(self.config.get("vwap_lookback_hours", 24) * 3600 * 1000)
        dev_band_1 = self.config.get("deviation_band_1", 1.0)  # σ
        dev_band_2 = self.config.get("deviation_band_2", 2.0)  # σ
        thresholds = self.config.get("thresholds", {"low": 0.5, "medium": 1.0, "high": 1.5})

        # --- Get session VWAP ---
        vwap = None
        if hasattr(self.store, 'get_session_vwap'):
            vwap = self.store.get_session_vwap(coin)

        if vwap is None:
            # Fallback: use mark_px rolling mean as proxy VWAP
            snaps = self.store.get_snapshots_window(coin, lookback_ms)
            if len(snaps) < 4:
                self.logger.debug("%s: insufficient data for VWAP", coin)
                return None
            prices = [s["mark_px"] for s in snaps if s.get("mark_px", 0) > 0]
            if not prices:
                return None
            vwap = sum(prices) / len(prices)
            self.logger.debug("%s: using mark_px mean as VWAP proxy: %.2f", coin, vwap)

        # --- Get VWAP std dev from OHLCV ---
        vwap_std = None
        if hasattr(self.store, 'get_vwap_std'):
            result = self.store.get_vwap_std(coin, lookback_ms)
            if result:
                vwap_mean, vwap_std = result

        # If no OHLCV std available, compute from snapshots
        if vwap_std is None:
            snaps = self.store.get_snapshots_window(coin, lookback_ms)
            prices = [s["mark_px"] for s in snaps if s.get("mark_px", 0) > 0]
            if len(prices) < 4:
                return None
            mean_p = sum(prices) / len(prices)
            variance = sum((p - mean_p) ** 2 for p in prices) / len(prices)
            import math
            vwap_std = math.sqrt(variance) if variance > 0 else None

        if vwap_std is None or vwap_std == 0:
            return None

        # --- Current price ---
        snaps = self.store.get_snapshots_window(coin, int(5 * 60 * 1000))  # last 5 min
        if not snaps:
            snaps = self.store.get_snapshots_window(coin, lookback_ms)
        current_price = snaps[-1]["mark_px"] if snaps else vwap

        if current_price <= 0 or vwap <= 0:
            return None

        # --- Compute deviation in σ units ---
        deviation = (current_price - vwap) / vwap_std  # σ above/below VWAP
        deviation_pct = (current_price - vwap) / vwap * 100

        # Store VWAP state as metadata for confluence engine
        vwap_state = {
            "vwap": vwap,
            "current_price": current_price,
            "deviation_sigma": deviation,
            "deviation_pct": deviation_pct,
            "vwap_std": vwap_std,
        }

        # At VWAP (within ±0.5σ): no signal — fair value, dampens other signals
        if abs(deviation) < 0.5:
            self.logger.debug(
                "%s: at VWAP (deviation=%.2fσ) — modifier: neutral", coin, deviation
            )
            return None  # No signal at fair value

        direction = "LONG_BIAS" if deviation < 0 else "SHORT_BIAS"
        abs_dev = abs(deviation)

        # Priority based on deviation magnitude
        priority = self._map_to_priority(abs_dev, thresholds)
        if priority is None:
            return None

        high_thresh = thresholds.get("high", 1.5)
        strength = min(abs_dev / (high_thresh * 2.0), 1.0)

        sigma_str = f"{deviation:+.1f}σ"
        pos = "below" if deviation < 0 else "above"
        band_desc = f"±{dev_band_1}σ" if abs_dev < dev_band_2 else f"±{dev_band_2}σ"

        if standalone:
            action = "standalone VWAP signal"
        else:
            confirm_word = "confirms LONG" if direction == "LONG_BIAS" else "confirms SHORT"
            action = f"VWAP modifier: {confirm_word} signals"

        message = (
            f"📐 VWAP DEVIATION — {coin} {direction}\n"
            f"Price {pos} VWAP: {sigma_str} ({deviation_pct:+.2f}%)\n"
            f"Band: {band_desc} — {action}."
        )

        self.logger.debug(
            "%s: VWAP %s deviation=%.2fσ (%.2f%%) standalone=%s",
            coin, direction, deviation, deviation_pct, standalone,
        )

        # Only emit SignalResult if standalone mode is enabled
        # In modifier mode, return None but the confluence engine will query VWAP state
        if not standalone:
            return None

        return SignalResult(
            signal_name=self.name,
            coin=coin,
            direction=direction,
            strength=strength,
            priority=priority,
            message=message,
            timestamp=time.time(),
            metadata={**vwap_state, "sub_signal": "vwap_deviation"},
        )

    def get_vwap_state(self, coin: str) -> Optional[dict]:
        """
        Called by the confluence engine to get VWAP modifier state.
        Returns VWAP metadata regardless of standalone setting.
        """
        lookback_ms = int(self.config.get("vwap_lookback_hours", 24) * 3600 * 1000)

        vwap = None
        if hasattr(self.store, 'get_session_vwap'):
            vwap = self.store.get_session_vwap(coin)

        if vwap is None:
            snaps = self.store.get_snapshots_window(coin, lookback_ms)
            prices = [s["mark_px"] for s in snaps if s.get("mark_px", 0) > 0]
            if not prices:
                return None
            vwap = sum(prices) / len(prices)

        snaps5m = self.store.get_snapshots_window(coin, int(5 * 60 * 1000))
        snaps_all = self.store.get_snapshots_window(coin, lookback_ms)
        current_price = (snaps5m[-1]["mark_px"] if snaps5m else
                         snaps_all[-1]["mark_px"] if snaps_all else vwap)

        prices = [s["mark_px"] for s in snaps_all if s.get("mark_px", 0) > 0]
        if len(prices) < 2 or vwap <= 0 or current_price <= 0:
            return None

        mean_p = sum(prices) / len(prices)
        import math
        variance = sum((p - mean_p) ** 2 for p in prices) / len(prices)
        std = math.sqrt(variance) if variance > 0 else 0.0

        deviation = (current_price - vwap) / std if std > 0 else 0.0
        deviation_pct = (current_price - vwap) / vwap * 100

        return {
            "vwap": vwap,
            "current_price": current_price,
            "deviation_sigma": deviation,
            "deviation_pct": deviation_pct,
            "vwap_std": std,
        }
