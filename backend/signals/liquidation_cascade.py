from __future__ import annotations

import statistics
import time
from typing import Optional

from signals.base import BaseSignal, SignalResult


class LiquidationCascadeSignal(BaseSignal):
    """Detects forced liquidation bursts and late-stage cascade exhaustion."""

    def evaluate(self, coin: str) -> Optional[SignalResult]:
        lookback_minutes = int(self.config.get("lookback_minutes", 30))
        baseline_minutes = int(self.config.get("baseline_lookback_minutes", 180))
        min_events = int(self.config.get("min_events", 6))
        intensity_z_threshold = float(self.config.get("intensity_z_threshold", 1.5))
        dominance_threshold = float(self.config.get("dominance_threshold", 0.6))
        acceleration_threshold = float(self.config.get("acceleration_threshold", 1.15))
        exhaustion_z_threshold = float(self.config.get("exhaustion_z_threshold", 2.5))
        exhaustion_decay_ratio = float(self.config.get("exhaustion_decay_ratio", 0.75))
        thresholds = self.config.get("thresholds", {"low": 1.0, "medium": 1.5, "high": 2.0})

        lookback_ms = lookback_minutes * 60 * 1000
        baseline_ms = baseline_minutes * 60 * 1000
        liquidations = self.store.get_liquidations_window(coin, baseline_ms)
        if len(liquidations) < min_events:
            self.logger.debug("%s: only %d liquidation events", coin, len(liquidations))
            return None

        end_ts = max(liq["ts"] for liq in liquidations)
        current_window_start = end_ts - lookback_ms
        prev_window_start = current_window_start - lookback_ms
        current_bucket = self._summarize_window(
            [liq for liq in liquidations if liq["ts"] >= current_window_start],
            lookback_ms,
        )
        prev_bucket = self._summarize_window(
            [liq for liq in liquidations if prev_window_start <= liq["ts"] < current_window_start],
            lookback_ms,
        )

        bucket_count = max(baseline_minutes // max(lookback_minutes, 1), 3)
        buckets = self._build_buckets(liquidations, baseline_ms, lookback_ms, bucket_count)
        if len(buckets) < 3:
            return None
        recent_events = current_bucket["count"]
        if recent_events < min_events:
            return None

        dominant_side = current_bucket["dominant_side"]
        dominant_share = current_bucket["dominant_share"]
        if dominant_side is None or dominant_share < dominance_threshold:
            return None

        historical_intensities = [bucket["intensity"] for bucket in buckets[:-1]]
        if len(historical_intensities) < 2:
            return None

        mean_intensity = statistics.mean(historical_intensities)
        std_intensity = statistics.stdev(historical_intensities) if len(historical_intensities) > 1 else 0.0
        current_intensity = current_bucket["intensity"]
        current_z = (current_intensity - mean_intensity) / std_intensity if std_intensity > 0 else 0.0
        prev_intensity = prev_bucket["intensity"]
        acceleration_ratio = (current_intensity / prev_intensity) if prev_intensity > 0 else 0.0

        recent_peak = max(bucket["intensity"] for bucket in buckets[-3:])
        previous_z = (prev_intensity - mean_intensity) / std_intensity if std_intensity > 0 else 0.0

        if (
            previous_z >= exhaustion_z_threshold
            and prev_intensity == recent_peak
            and current_intensity <= prev_intensity * exhaustion_decay_ratio
            and current_bucket["dominant_side"] == prev_bucket["dominant_side"]
        ):
            direction = "LONG_BIAS" if dominant_side == "B" else "SHORT_BIAS"
            score = max(previous_z, dominant_share / max(dominance_threshold, 0.01))
            return self._build_result(
                coin=coin,
                direction=direction,
                thresholds=thresholds,
                score=score,
                message=(
                    f"💥 LIQUIDATION EXHAUSTION — {coin} {direction}\n"
                    f"Liq burst is decaying after a peak flush ({previous_z:+.2f}σ).\n"
                    f"Forced flow looks spent; reversal setup forming."
                ),
                metadata={
                    "sub_signal": "cascade_exhaustion",
                    "dominant_side": dominant_side,
                    "dominant_share": dominant_share,
                    "current_intensity": current_intensity,
                    "prev_intensity": prev_intensity,
                    "intensity_z_score": current_z,
                    "peak_z_score": previous_z,
                    "event_count": recent_events,
                },
            )

        if current_z < intensity_z_threshold or acceleration_ratio < acceleration_threshold:
            return None

        direction = "SHORT_BIAS" if dominant_side == "B" else "LONG_BIAS"
        score = max(current_z, acceleration_ratio, dominant_share / max(dominance_threshold, 0.01))
        return self._build_result(
            coin=coin,
            direction=direction,
            thresholds=thresholds,
            score=score,
            message=(
                f"⛓ LIQUIDATION CASCADE — {coin} {direction}\n"
                f"Forced-liquidation intensity: {current_z:+.2f}σ | accel: {acceleration_ratio:.2f}x\n"
                f"Cascade pressure remains one-sided."
            ),
            metadata={
                "sub_signal": "cascade_continuation",
                "dominant_side": dominant_side,
                "dominant_share": dominant_share,
                "current_intensity": current_intensity,
                "prev_intensity": prev_intensity,
                "intensity_z_score": current_z,
                "acceleration_ratio": acceleration_ratio,
                "event_count": recent_events,
            },
        )

    def _build_buckets(
        self,
        liquidations: list[dict],
        baseline_ms: int,
        bucket_ms: int,
        bucket_count: int,
    ) -> list[dict]:
        if not liquidations:
            return []
        end_ts = max(liq["ts"] for liq in liquidations)
        start_ts = end_ts - baseline_ms
        buckets: list[dict] = []
        for idx in range(bucket_count):
            bucket_start = start_ts + idx * bucket_ms
            bucket_end = bucket_start + bucket_ms
            items = [liq for liq in liquidations if bucket_start <= liq["ts"] < bucket_end]
            buckets.append(self._summarize_window(items, bucket_ms))
        return buckets

    def _summarize_window(self, liquidations: list[dict], window_ms: int) -> dict:
        long_notional = sum(liq["notional"] for liq in liquidations if liq["side"] == "B")
        short_notional = sum(liq["notional"] for liq in liquidations if liq["side"] == "S")
        total_notional = long_notional + short_notional
        dominant_notional = max(long_notional, short_notional)
        dominant_side = None
        if total_notional > 0:
            dominant_side = "B" if long_notional >= short_notional else "S"
        return {
            "count": len(liquidations),
            "long_notional": long_notional,
            "short_notional": short_notional,
            "total_notional": total_notional,
            "dominant_side": dominant_side,
            "dominant_share": (dominant_notional / total_notional) if total_notional > 0 else 0.0,
            "intensity": total_notional / max(window_ms / 60_000, 1),
        }

    def _build_result(
        self,
        coin: str,
        direction: str,
        thresholds: dict,
        score: float,
        message: str,
        metadata: dict,
    ) -> Optional[SignalResult]:
        priority = self._map_to_priority(score, thresholds)
        if priority is None:
            return None
        return SignalResult(
            signal_name=self.name,
            coin=coin,
            direction=direction,
            strength=min(score / (thresholds.get("high", 2.0) * 1.5), 1.0),
            priority=priority,
            message=message,
            timestamp=time.time(),
            metadata=metadata,
        )
