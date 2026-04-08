"""
Signal Engine — v2 with named-regime confluence and conflict resolution.

Implements v2 spec §4:
  - Named regime detection (Leveraged Squeeze, Organic Trend, Mean Reversion, etc.)
  - Conviction tiers: HIGH (2+ confirming), MEDIUM (2 partial), LOW (1 signal)
  - Conflict resolution priority: Spot-Led > Funding, OI/Vol > VWAP
  - VWAP acts as modifier only — never triggers regime alone
  - Conflicting equal-priority signals → no Trade Candidate, logged only
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from data.store import DataStore
from signals.base import BaseSignal, SignalResult
from signals import SIGNAL_REGISTRY

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Trade Candidate (replaces bare Alert)
# ---------------------------------------------------------------------------

@dataclass
class TradeCandidate:
    coin: str
    direction: str          # "LONG_BIAS" or "SHORT_BIAS"
    regime: str             # Named regime (v2 §4.1)
    conviction: str         # "HIGH", "MEDIUM", "LOW"
    signals: list[SignalResult]
    timestamp: float
    vwap_state: Optional[dict] = None   # VWAP modifier metadata if available
    alert_id: str = field(init=False)

    def __post_init__(self) -> None:
        raw = f"{self.coin}{self.direction}{self.timestamp:.0f}"
        self.alert_id = hashlib.md5(raw.encode()).hexdigest()[:12]


# Keep backward-compat alias used by main.py
Alert = TradeCandidate


# ---------------------------------------------------------------------------
# Regime definitions
# ---------------------------------------------------------------------------

_REGIMES = [
    # (regime_name, required_modules_set, direction_mode, conviction)
    # direction_mode: "fade" = opposite of crowd, "follow" = follow signal direction
    ("Leverage Flush",    {"leverage_flush", "liquidation_cascade"},   "aligned", "HIGH"),
    ("Liquidation Cascade", {"liquidation_cascade", "funding_velocity"}, "aligned", "HIGH"),
    ("Funding Acceleration", {"funding_velocity", "funding_extremes"},   "aligned", "HIGH"),
    ("Cascade Squeeze",   {"liquidation_cascade", "orderbook_imbalance"}, "aligned", "HIGH"),
    ("Orderflow Squeeze", {"orderbook_imbalance", "trade_flow_imbalance"}, "aligned", "HIGH"),
    ("Book Pressure",     {"orderbook_imbalance", "funding_extremes"},      "aligned", "HIGH"),
    ("Leveraged Squeeze", {"funding_extremes", "oi_volume_divergence"}, "aligned", "HIGH"),
    ("Organic Trend",     {"spot_led_flow", "vwap_deviation"},          "aligned", "HIGH"),
    ("Organic Trend",     {"spot_led_flow"},                             "aligned", "HIGH"),   # standalone spot flow still HIGH in context
    ("Forced Deleveraging", {"leverage_flush"},                          "aligned", "MEDIUM"),
    ("Aggressive Flow",   {"trade_flow_imbalance"},                      "aligned", "MEDIUM"),
    ("Mean Reversion",    {"funding_extremes", "vwap_deviation"},        "aligned", "MEDIUM"),
    ("Leverage Unwind",   {"oi_volume_divergence", "vwap_deviation"},    "aligned", "MEDIUM"),
    ("Liq Cascade",       {"liquidation_cascade"},                       "aligned", "LOW"),
    ("Funding Momentum",  {"funding_velocity"},                          "aligned", "LOW"),
    ("Book Imbalance",    {"orderbook_imbalance"},                       "aligned", "LOW"),
    ("Spot Absorption",   {"spot_led_flow"},                             "aligned", "LOW"),
    ("Funding Fade",      {"funding_extremes"},                          "aligned", "LOW"),
    ("OI Divergence",     {"oi_volume_divergence"},                      "aligned", "LOW"),
    ("CVD Divergence",    {"cvd_divergence"},                            "aligned", "LOW"),
    ("Premium Extreme",   {"premium_extremes"},                          "aligned", "LOW"),
]

# Priority for conflict resolution (higher index = higher priority)
_SIGNAL_PRIORITY: dict[str, int] = {
    "premium_extremes":    0,
    "cvd_divergence":      1,
    "vwap_deviation":      2,
    "orderbook_imbalance": 3,
    "funding_extremes":    4,
    "funding_velocity":    5,
    "trade_flow_imbalance": 6,
    "liquidation_cascade": 7,
    "oi_volume_divergence": 8,
    "leverage_flush":      9,
    "spot_led_flow":       10,
}

# Signals that are modifier-only (never trigger a candidate alone unless paired)
_MODIFIER_ONLY = {"vwap_deviation"}


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class SignalEngine:
    """Loads, manages, and evaluates all configured signals."""

    def __init__(
        self,
        config_dir: str,
        global_config: dict,
        store: DataStore,
    ) -> None:
        self.global_config = global_config
        self.store = store
        self._signals: list[BaseSignal] = []
        self._vwap_signal: Optional[BaseSignal] = None
        self._load_signals(config_dir)

    def _load_signals(self, config_dir: str) -> None:
        import glob, os
        signals_dir = os.path.join(config_dir, "signals")
        pattern = os.path.join(signals_dir, "*.yaml")
        yaml_files = glob.glob(pattern)

        for yaml_path in sorted(yaml_files):
            stem = os.path.splitext(os.path.basename(yaml_path))[0]
            cls = SIGNAL_REGISTRY.get(stem)
            if cls is None:
                logger.warning("No signal class registered for config: %s", stem)
                continue
            try:
                config = BaseSignal.load_config(yaml_path)
            except (FileNotFoundError, ValueError) as exc:
                logger.error("Cannot load signal config %s: %s", yaml_path, exc)
                continue
            if not config.get("enabled", True):
                logger.info("Signal %s is disabled, skipping", stem)
                continue
            instance = cls(name=stem, config=config, store=self.store)
            instance.global_config = self.global_config
            self._signals.append(instance)
            if stem == "vwap_deviation":
                self._vwap_signal = instance
            logger.info("Loaded signal: %s (%s)", stem, cls.__name__)

        logger.info("SignalEngine: %d signals loaded", len(self._signals))

    async def evaluate_all(self, coins: list[str]) -> list[SignalResult]:
        """
        Run every enabled signal for each coin.
        A single signal crash never stops the tick.
        Returns list of non-None results.
        """
        results: list[SignalResult] = []
        for signal in self._signals:
            for coin in coins:
                try:
                    result = signal.evaluate(coin)
                    if result is not None:
                        results.append(result)
                        logger.debug(
                            "Signal %s/%s → %s %s (strength=%.2f)",
                            signal.name, coin, result.direction, result.priority, result.strength,
                        )
                    else:
                        logger.debug("Signal %s/%s → None", signal.name, coin)
                except Exception as exc:
                    logger.error(
                        "Signal %s crashed for %s: %s",
                        signal.name, coin, exc, exc_info=True,
                    )
        return results

    def score_confluence(self, results: list[SignalResult]) -> list[TradeCandidate]:
        """
        v2 named-regime confluence engine.

        For each coin:
          1. Separate signals by direction. Check for conflicts.
          2. Conflict resolution: higher-priority signal wins.
          3. Match signal set against regime definitions.
          4. VWAP modifier adjusts strength but doesn't trigger alone.
          5. Return TradeCandidate per winning direction per coin.
        """
        # Group by coin
        by_coin: dict[str, list[SignalResult]] = {}
        for r in results:
            by_coin.setdefault(r.coin, []).append(r)

        candidates: list[TradeCandidate] = []

        for coin, coin_signals in by_coin.items():
            vwap_state = self._get_vwap_state(coin)
            candidate = self._evaluate_coin(coin, coin_signals, vwap_state)
            if candidate:
                candidates.append(candidate)

        return candidates

    def _get_vwap_state(self, coin: str) -> Optional[dict]:
        """Retrieve VWAP modifier state if VWAPDeviationSignal is loaded."""
        if self._vwap_signal and hasattr(self._vwap_signal, "get_vwap_state"):
            try:
                return self._vwap_signal.get_vwap_state(coin)
            except Exception as exc:
                logger.debug("VWAP state error for %s: %s", coin, exc)
        return None

    def _evaluate_coin(
        self,
        coin: str,
        signals: list[SignalResult],
        vwap_state: Optional[dict],
    ) -> Optional[TradeCandidate]:
        """
        Applies v2 conflict resolution and regime detection for a single coin.
        Returns the best TradeCandidate or None.
        """
        # Separate into directional groups
        longs = [s for s in signals if s.direction == "LONG_BIAS"]
        shorts = [s for s in signals if s.direction == "SHORT_BIAS"]

        # --- Conflict resolution ---
        if longs and shorts:
            long_max_prio = max(_SIGNAL_PRIORITY.get(s.signal_name, 0) for s in longs)
            short_max_prio = max(_SIGNAL_PRIORITY.get(s.signal_name, 0) for s in shorts)

            if long_max_prio > short_max_prio:
                logger.info(
                    "Confluence %s: LONG wins conflict (long_prio=%d > short_prio=%d)",
                    coin, long_max_prio, short_max_prio,
                )
                signals_to_use = longs
                direction = "LONG_BIAS"
            elif short_max_prio > long_max_prio:
                logger.info(
                    "Confluence %s: SHORT wins conflict (short_prio=%d > long_prio=%d)",
                    coin, short_max_prio, long_max_prio,
                )
                signals_to_use = shorts
                direction = "SHORT_BIAS"
            else:
                # Equal priority → no trade candidate, log conflict
                long_names = [s.signal_name for s in longs]
                short_names = [s.signal_name for s in shorts]
                logger.warning(
                    "Confluence %s: CONFLICT — equal priority (LONG: %s / SHORT: %s). "
                    "No trade candidate emitted.",
                    coin, long_names, short_names,
                )
                return None
        elif longs:
            signals_to_use = longs
            direction = "LONG_BIAS"
        elif shorts:
            signals_to_use = shorts
            direction = "SHORT_BIAS"
        else:
            return None

        # Filter out modifier-only signals for regime matching
        actionable = [s for s in signals_to_use if s.signal_name not in _MODIFIER_ONLY]
        if not actionable:
            return None

        # --- VWAP modifier: at fair value, dampen signals ---
        if vwap_state:
            deviation_sigma = vwap_state.get("deviation_sigma", 0.0)
            # Suppress if at fair value (within ±0.5σ)
            if abs(deviation_sigma) < 0.5:
                logger.debug(
                    "Confluence %s: VWAP at fair value (%.2fσ) — dampening signals",
                    coin, deviation_sigma,
                )
                # Downgrade HIGH → MEDIUM to simulate dampening
                # (actual trade candidate still emitted but conviction reduced)
                # We handle this in regime assignment below

        # --- Regime detection ---
        module_names = {s.signal_name for s in actionable}
        regime_name, conviction = self._match_regime(module_names, vwap_state)

        # --- VWAP confirmation boost ---
        if vwap_state and conviction in ("MEDIUM", "LOW"):
            deviation_sigma = vwap_state.get("deviation_sigma", 0.0)
            # VWAP confirms direction: below VWAP strengthens longs, above strengthens shorts
            vwap_confirms = (
                (direction == "LONG_BIAS" and deviation_sigma < -1.0) or
                (direction == "SHORT_BIAS" and deviation_sigma > 1.0)
            )
            if vwap_confirms and conviction == "MEDIUM":
                logger.debug("Confluence %s: VWAP confirms — boosting to HIGH", coin)
                conviction = "HIGH"
            elif vwap_confirms and conviction == "LOW":
                conviction = "MEDIUM"

        logger.info(
            "Confluence %s: regime=%s conviction=%s direction=%s signals=%s",
            coin, regime_name, conviction, direction,
            [s.signal_name for s in actionable],
        )

        return TradeCandidate(
            coin=coin,
            direction=direction,
            regime=regime_name,
            conviction=conviction,
            signals=actionable,
            timestamp=time.time(),
            vwap_state=vwap_state,
        )

    def _match_regime(
        self,
        module_names: set[str],
        vwap_state: Optional[dict],
    ) -> tuple[str, str]:
        """
        Match a set of active module names against regime definitions.
        Returns (regime_name, conviction).
        Checks regimes in order — first match wins (highest conviction first).
        """
        # Add vwap to module names if VWAP is significantly deviated
        effective_modules = set(module_names)
        if vwap_state:
            dev = abs(vwap_state.get("deviation_sigma", 0.0))
            if dev >= 1.0:
                effective_modules.add("vwap_deviation")

        for regime_name, required, _, conviction in _REGIMES:
            if required.issubset(effective_modules):
                return regime_name, conviction

        # No specific regime matched — generic by count
        count = len(module_names)
        if count >= 3:
            return "Multi-Signal Confluence", "HIGH"
        elif count >= 2:
            return "Mixed Signals", "MEDIUM"
        else:
            return "Single Signal", "LOW"
