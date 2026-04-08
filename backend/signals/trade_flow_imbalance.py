from __future__ import annotations

import statistics
import time
from typing import Optional

from signals.base import BaseSignal, SignalResult


class TradeFlowImbalanceSignal(BaseSignal):
    """Detects directional aggression, whale skew, and absorption from trade ticks."""

    def evaluate(self, coin: str) -> Optional[SignalResult]:
        lookback_minutes = self._resolve_minutes()
        lookback_ms = lookback_minutes * 60 * 1000
        min_trades = self._resolve_count("min_trades", default=200)
        whale_threshold = float(self.config.get("whale_threshold_usd", 50_000))
        whale_skew_threshold = float(self.config.get("whale_skew_threshold", 0.65))
        delta_z_threshold = float(self.config.get("delta_z_threshold", 1.5))
        absorption_price_tolerance_pct = float(self.config.get("absorption_price_tolerance_pct", 0.1))
        thresholds = self.config.get("thresholds", {"low": 1.0, "medium": 1.5, "high": 2.0})

        ticks = self.store.get_trade_ticks(coin, lookback_ms)
        if len(ticks) < min_trades:
            self.logger.debug("%s: only %d trade ticks (need %d)", coin, len(ticks), min_trades)
            return None

        buy_notional = sum(tick["notional"] for tick in ticks if tick["side"] == "B")
        sell_notional = sum(tick["notional"] for tick in ticks if tick["side"] != "B")
        total_notional = buy_notional + sell_notional
        if total_notional <= 0:
            return None

        price_start = ticks[0]["px"]
        price_end = ticks[-1]["px"]
        if price_start <= 0:
            return None
        price_change_pct = ((price_end - price_start) / price_start) * 100
        delta_ratio = (buy_notional - sell_notional) / total_notional

        best_signal: Optional[tuple[float, SignalResult]] = None

        delta_history = self._delta_ratio_history(coin, lookback_ms)
        if len(delta_history) >= 2:
            std = statistics.stdev(delta_history)
            if std > 0:
                latest_delta = delta_history[-1]
                delta_z_score = (latest_delta - statistics.mean(delta_history[:-1])) / std
                if abs(delta_z_score) >= delta_z_threshold:
                    direction = "LONG_BIAS" if latest_delta > 0 else "SHORT_BIAS"
                    result = self._build_result(
                        coin=coin,
                        direction=direction,
                        thresholds=thresholds,
                        score=max(abs(delta_z_score), abs(latest_delta) / max(whale_skew_threshold, 0.01)),
                        message=(
                            f"⚔ TRADE FLOW IMBALANCE — {coin} {direction}\n"
                            f"Delta ratio: {latest_delta:+.2f} | z-score: {delta_z_score:+.2f}\n"
                            f"Aggressive flow is materially one-sided."
                        ),
                        metadata={
                            "sub_signal": "delta_imbalance",
                            "delta_ratio": latest_delta,
                            "delta_z_score": delta_z_score,
                            "price_pct_change": price_change_pct,
                            "buy_notional": buy_notional,
                            "sell_notional": sell_notional,
                        },
                    )
                    best_signal = self._pick_best(best_signal, abs(delta_z_score), result)

        whale_ticks = [tick for tick in ticks if tick["notional"] >= whale_threshold]
        if whale_ticks:
            whale_buy = sum(tick["notional"] for tick in whale_ticks if tick["side"] == "B")
            whale_sell = sum(tick["notional"] for tick in whale_ticks if tick["side"] != "B")
            whale_total = whale_buy + whale_sell
            whale_ratio = abs(whale_buy - whale_sell) / whale_total if whale_total else 0.0
            if whale_total and whale_ratio >= whale_skew_threshold:
                direction = "LONG_BIAS" if whale_buy > whale_sell else "SHORT_BIAS"
                result = self._build_result(
                    coin=coin,
                    direction=direction,
                    thresholds=thresholds,
                    score=whale_ratio / whale_skew_threshold,
                    message=(
                        f"🐋 WHALE FLOW SKEW — {coin} {direction}\n"
                        f"Large-trade skew: {whale_ratio:.2f} across {len(whale_ticks)} whale prints\n"
                        f"Large aggressive orders are leaning {direction.lower()}."
                    ),
                    metadata={
                        "sub_signal": "whale_skew",
                        "whale_ratio": whale_ratio,
                        "whale_trade_count": len(whale_ticks),
                        "whale_buy_notional": whale_buy,
                        "whale_sell_notional": whale_sell,
                        "price_pct_change": price_change_pct,
                    },
                )
                best_signal = self._pick_best(best_signal, whale_ratio, result)

        abs_delta_ratio = abs(delta_ratio)
        if abs(price_change_pct) <= absorption_price_tolerance_pct and abs_delta_ratio >= 0.2:
            direction = "LONG_BIAS" if delta_ratio < 0 else "SHORT_BIAS"
            result = self._build_result(
                coin=coin,
                direction=direction,
                thresholds=thresholds,
                score=abs_delta_ratio / 0.2,
                message=(
                    f"🧱 ABSORPTION FLOW — {coin} {direction}\n"
                    f"Delta ratio: {delta_ratio:+.2f} | Price change: {price_change_pct:+.2f}%\n"
                    f"Aggression is being absorbed without directional follow-through."
                ),
                metadata={
                    "sub_signal": "absorption",
                    "delta_ratio": delta_ratio,
                    "price_pct_change": price_change_pct,
                    "buy_notional": buy_notional,
                    "sell_notional": sell_notional,
                },
            )
            best_signal = self._pick_best(best_signal, abs_delta_ratio, result)

        return best_signal[1] if best_signal else None

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

    def _delta_ratio_history(self, coin: str, lookback_ms: int) -> list[float]:
        history_ticks = self.store.get_trade_ticks(coin, lookback_ms * 6)
        if not history_ticks:
            return []
        start_ts = history_ticks[0]["ts"]
        ratios: list[float] = []
        for bucket_idx in range(6):
            bucket_start = start_ts + bucket_idx * lookback_ms
            bucket_end = bucket_start + lookback_ms
            bucket_ticks = [
                tick for tick in history_ticks
                if bucket_start <= tick["ts"] < bucket_end
            ]
            if not bucket_ticks:
                continue
            buy_notional = sum(tick["notional"] for tick in bucket_ticks if tick["side"] == "B")
            sell_notional = sum(tick["notional"] for tick in bucket_ticks if tick["side"] != "B")
            total = buy_notional + sell_notional
            if total > 0:
                ratios.append((buy_notional - sell_notional) / total)
        return ratios

    def _pick_best(
        self,
        current: Optional[tuple[float, SignalResult]],
        score: float,
        result: Optional[SignalResult],
    ) -> Optional[tuple[float, SignalResult]]:
        if result is None:
            return current
        if current is None or score > current[0]:
            return (score, result)
        return current

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
            return int(self.config.get(f"daily_{key}", max(value * 4, value)))
        return value
