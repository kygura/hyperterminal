"""
Alert Manager — v2 format, cooldowns, and conviction-based routing.

v2 rules:
  - HIGH conviction: Telegram alert + terminal print
  - MEDIUM conviction: Telegram alert + terminal print
  - LOW conviction: terminal print only (no Telegram)

Format matches v2 spec §6 exactly.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Optional

from engine.signal_engine import TradeCandidate
from signals.base import SignalResult

# Optional deep-link base URL — set DASHBOARD_URL=https://your-domain.com in .env
_DASHBOARD_URL = os.getenv("DASHBOARD_URL", "").rstrip("/")

logger = logging.getLogger(__name__)

# Direction display
_DIR_ARROW = {"LONG_BIAS": "⬆ LONG", "SHORT_BIAS": "⬇ SHORT"}
_DIR_EMOJI = {"LONG_BIAS": "🟢", "SHORT_BIAS": "🔴"}
_CONVICTION_EMOJI = {"HIGH": "🔴", "MEDIUM": "🟡", "LOW": "🟢"}

# All signal slots for the "Signals:" block in v2 format
_SIGNAL_DISPLAY_ORDER = [
    ("funding_extremes",     "Funding Rate"),
    ("funding_velocity",     "Funding Vel"),
    ("liquidation_cascade",  "Liq Cascade"),
    ("leverage_flush",       "Lev Flush"),
    ("orderbook_imbalance",  "Book Imb"),
    ("oi_volume_divergence", "OI/Vol Div"),
    ("trade_flow_imbalance", "Trade Flow"),
    ("spot_led_flow",        "Spot Flow"),
    ("vwap_deviation",       "VWAP"),
    ("cvd_divergence",       "CVD"),
    ("premium_extremes",     "Premium"),
]

_LINE = "──────────────────────────────"


class AlertManager:
    """Tracks cooldowns and formats alerts per v2 spec."""

    def __init__(self, cooldown_seconds: int = 300, cadence: str = "hourly") -> None:
        self.cooldown_seconds = cooldown_seconds
        self.cadence = cadence
        self._last_fire: dict[tuple[str, str], float] = {}
        self._last_bucket: dict[tuple[str, str], str] = {}
        self._total_alerts = 0

    def _bucket_for(self, timestamp: float) -> str:
        tm = time.gmtime(timestamp)
        if self.cadence == "weekly":
            iso_year, iso_week, _ = time.strftime("%G %V %u", tm).split()
            return f"{iso_year}-W{iso_week}"
        if self.cadence == "daily":
            return time.strftime("%Y-%m-%d", tm)
        return time.strftime("%Y-%m-%dT%H", tm)

    def should_fire(self, candidate: TradeCandidate) -> bool:
        key = (candidate.coin, candidate.direction)
        bucket = self._bucket_for(candidate.timestamp)
        if self._last_bucket.get(key) == bucket:
            logger.debug(
                "AlertManager: suppressing %s %s — already emitted for %s bucket",
                candidate.coin,
                candidate.direction,
                bucket,
            )
            return False
        last = self._last_fire.get(key, 0.0)
        remaining = self.cooldown_seconds - (time.time() - last)
        if remaining > 0:
            logger.debug(
                "AlertManager: suppressing %s %s — cooldown %.0fs remaining",
                candidate.coin, candidate.direction, remaining,
            )
            return False
        return True

    def record_fire(self, candidate: TradeCandidate) -> None:
        key = (candidate.coin, candidate.direction)
        self._last_fire[key] = time.time()
        self._last_bucket[key] = self._bucket_for(candidate.timestamp)
        self._total_alerts += 1

    @property
    def total_alerts(self) -> int:
        return self._total_alerts

    def should_telegram(self, candidate: TradeCandidate) -> bool:
        """Only MEDIUM and HIGH conviction trigger Telegram."""
        return candidate.conviction in ("HIGH", "MEDIUM")

    def format_alert(self, candidate: TradeCandidate) -> str:
        """
        Format a TradeCandidate as plain-text per v2 spec §6.
        Suitable for terminal output and Telegram (plain text).
        """
        dir_str = _DIR_ARROW.get(candidate.direction, candidate.direction)
        conv_emoji = _CONVICTION_EMOJI.get(candidate.conviction, "⚪")
        signal_count = len(candidate.signals)
        total_signals = len([s for s in _SIGNAL_DISPLAY_ORDER])

        active_names = {s.signal_name for s in candidate.signals}

        # Build signal lines
        signal_lines = _build_signal_lines(candidate)

        # Price & VWAP
        price_line = "Price:       N/A"
        vwap_line = "VWAP:        N/A"

        if candidate.vwap_state:
            vwap = candidate.vwap_state.get("vwap")
            price = candidate.vwap_state.get("current_price")
            if vwap and price:
                dev_pct = (price - vwap) / vwap * 100
                price_line = f"Price:       ${price:,.2f}"
                vwap_line = f"VWAP:        ${vwap:,.2f} ({dev_pct:+.1f}%)"
        elif candidate.signals:
            # Try to get price from signal metadata
            for sig in candidate.signals:
                mark = sig.metadata.get("mark_px") or sig.metadata.get("current_price")
                if mark:
                    price_line = f"Price:       ${mark:,.2f}"
                    break

        ts_str = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(candidate.timestamp))

        lines = [
            f"🚨 SIGNAL ENGINE — Trade Candidate",
            _LINE,
            f"Asset:       {candidate.coin}-PERP",
            f"Direction:   {dir_str}",
            f"Regime:      {candidate.regime}",
            f"Conviction:  {conv_emoji} {candidate.conviction} ({signal_count}/{total_signals} signals)",
            _LINE,
            "Signals:",
        ]
        lines.extend(signal_lines)
        lines.extend([
            _LINE,
            price_line,
            vwap_line,
            f"Time:        {ts_str}",
        ])

        # Deep-link back to dashboard signal detail (Step 8)
        signal_id = getattr(candidate, "signal_id", None) or getattr(candidate, "id", None)
        if _DASHBOARD_URL and signal_id:
            lines.append(f"🔗 {_DASHBOARD_URL}/signals?id={signal_id}")
        elif _DASHBOARD_URL:
            lines.append(f"🔗 {_DASHBOARD_URL}/signals")

        return "\n".join(lines)

    def format_terminal_compact(self, candidate: TradeCandidate) -> str:
        """
        Compact terminal summary for LOW conviction signals.
        Does not fire Telegram.
        """
        dir_str = _DIR_ARROW.get(candidate.direction, candidate.direction)
        signal_names = ", ".join(s.signal_name for s in candidate.signals)
        ts_str = time.strftime("%H:%M", time.gmtime(candidate.timestamp))
        return (
            f"[{ts_str}] {candidate.coin} {dir_str} — "
            f"{candidate.regime} [{candidate.conviction}] "
            f"({signal_names})"
        )


def _build_signal_lines(candidate: TradeCandidate) -> list[str]:
    """
    Build signal status lines matching v2 format:
      ✅ Funding Rate: -2.3σ (extreme short)
      ✅ OI/Vol Div:  OI +8.2%, Vol -14.3%
      ➖ Spot Flow:   Neutral
      ✅ VWAP:        Price at -1.4σ (confirms)
    Only show signals that are relevant to this candidate's signals set.
    """
    active_by_name = {s.signal_name: s for s in candidate.signals}
    lines = []

    for name, label in _SIGNAL_DISPLAY_ORDER:
        sig = active_by_name.get(name)
        if sig is None:
            # Don't show signals not loaded at all — only show relevant ones
            continue
        detail = _signal_detail(name, sig)
        lines.append(f"  ✅ {label:<14} {detail}")

    return lines


def _signal_detail(name: str, sig: SignalResult) -> str:
    """Extract a concise one-line detail from signal metadata."""
    meta = sig.metadata

    if name == "funding_extremes":
        z = meta.get("z_score", 0.0)
        rate_pct = meta.get("latest_rate", 0.0) * 100
        ext = "extreme long" if z > 0 else "extreme short"
        return f"{z:+.1f}σ ({rate_pct:.4f}% — {ext})"

    elif name == "funding_velocity":
        velocity = meta.get("recent_velocity", 0.0)
        acceleration = meta.get("acceleration", 0.0)
        return f"vel {velocity:+.5f}, accel {acceleration:+.5f}"

    elif name == "liquidation_cascade":
        sub_signal = meta.get("sub_signal", "cascade")
        z = meta.get("intensity_z_score", meta.get("peak_z_score", 0.0))
        return f"{sub_signal}: {z:+.2f}σ"

    elif name == "leverage_flush":
        oi = meta.get("oi_pct_change", 0.0)
        price = meta.get("price_pct_change", 0.0)
        return f"{meta.get('sub_signal', 'flush')}: OI {oi:+.1f}%, Px {price:+.1f}%"

    elif name == "orderbook_imbalance":
        ratio = meta.get("imbalance_ratio", 0.0)
        persistence = meta.get("persistence_count", 0)
        spread = meta.get("spread", 0.0)
        return f"Ratio {ratio:.2f}, spread {spread:.2f}, persistence {persistence}"

    elif name == "oi_volume_divergence":
        oi = meta.get("oi_pct", 0.0)
        vol = meta.get("vol_pct_change", 0.0)
        return f"OI {oi:+.1f}%, Vol {vol:+.0f}%"

    elif name == "trade_flow_imbalance":
        sub_signal = meta.get("sub_signal", "flow")
        delta_ratio = meta.get("delta_ratio")
        whale_ratio = meta.get("whale_ratio")
        if delta_ratio is not None:
            return f"{sub_signal}: delta {delta_ratio:+.2f}"
        if whale_ratio is not None:
            return f"{sub_signal}: skew {whale_ratio:.2f}"
        return sub_signal

    elif name == "spot_led_flow":
        surge = meta.get("vol_surge_pct", 0.0)
        oi = meta.get("oi_pct", 0.0)
        return f"Vol surge +{surge:.0f}%, OI {oi:+.1f}% (organic)"

    elif name == "vwap_deviation":
        dev = meta.get("deviation_sigma", 0.0)
        confirms = "confirms LONG" if dev < 0 else "confirms SHORT"
        return f"Price at {dev:+.1f}σ ({confirms})"

    elif name == "cvd_divergence":
        price_pct = meta.get("price_pct_change", 0.0)
        cvd_trend = meta.get("cvd_trend_pct", 0.0)
        return f"Price {price_pct:+.1f}%, CVD trend {cvd_trend:+.0f}%"

    elif name == "premium_extremes":
        prem = meta.get("premium_pct", 0.0)
        return f"Premium {prem:+.3f}%"

    return sig.message.split("\n")[0] if sig.message else ""
