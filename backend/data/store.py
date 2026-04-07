"""
In-memory rolling data store using collections.deque.
All window methods are time-based and handle empty deques gracefully.
"""

from __future__ import annotations

import logging
import math
import time
from collections import deque
from typing import Optional

logger = logging.getLogger(__name__)


def _is_valid_number(v) -> bool:
    """Return True iff v is a finite float-like value."""
    try:
        f = float(v)
        return math.isfinite(f)
    except (TypeError, ValueError):
        return False


class DataStore:
    """Thread-safe-ish rolling store (single async event loop, no locks needed)."""

    def __init__(self) -> None:
        self.funding_history: dict[str, deque] = {}   # coin -> deque[{rate, premium, time}]
        self.trades: dict[str, deque] = {}             # coin -> deque[{side, px, sz, time}]
        self.asset_snapshots: dict[str, deque] = {}    # coin -> deque[{funding, oi, mark_px, oracle_px, premium, time}]
        self.liquidations: deque = deque(maxlen=5000)   # deque[{coin, side, px, sz, time}]

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _ensure_deque(self, store: dict, coin: str, maxlen: int) -> deque:
        if coin not in store:
            store[coin] = deque(maxlen=maxlen)
        return store[coin]

    # ------------------------------------------------------------------
    # Add methods
    # ------------------------------------------------------------------

    def add_funding(self, coin: str, rate: float, premium: float, ts: int) -> None:
        if not _is_valid_number(rate) or not _is_valid_number(premium) or not _is_valid_number(ts):
            logger.debug("DataStore: rejected invalid funding entry for %s", coin)
            return
        dq = self._ensure_deque(self.funding_history, coin, maxlen=1000)
        dq.append({"rate": float(rate), "premium": float(premium), "time": int(ts)})

    def add_trade(self, coin: str, side: str, px: float, sz: float, ts: int) -> None:
        if not _is_valid_number(px) or not _is_valid_number(sz) or not _is_valid_number(ts):
            logger.debug("DataStore: rejected invalid trade for %s", coin)
            return
        dq = self._ensure_deque(self.trades, coin, maxlen=10000)
        dq.append({"side": str(side), "px": float(px), "sz": float(sz), "time": int(ts)})

    def add_snapshot(
        self,
        coin: str,
        funding: float,
        oi: float,
        mark_px: float,
        oracle_px: float,
        premium: float,
    ) -> None:
        for v in (funding, oi, mark_px, oracle_px, premium):
            if not _is_valid_number(v):
                logger.debug("DataStore: rejected invalid snapshot for %s", coin)
                return
        ts = time.time()
        dq = self._ensure_deque(self.asset_snapshots, coin, maxlen=500)
        dq.append({
            "funding": float(funding),
            "oi": float(oi),
            "mark_px": float(mark_px),
            "oracle_px": float(oracle_px),
            "premium": float(premium),
            "time": ts,
        })

    def add_liquidation(self, coin: str, side: str, px: float, sz: float, ts: int) -> None:
        if not _is_valid_number(px) or not _is_valid_number(sz) or not _is_valid_number(ts):
            logger.debug("DataStore: rejected invalid liquidation for %s", coin)
            return
        self.liquidations.append({
            "coin": str(coin),
            "side": str(side),
            "px": float(px),
            "sz": float(sz),
            "time": int(ts),
        })

    # ------------------------------------------------------------------
    # Window query helpers
    # ------------------------------------------------------------------

    def get_funding_window(self, coin: str, lookback_ms: int) -> list[dict]:
        """Return funding entries within the last lookback_ms milliseconds."""
        dq = self.funding_history.get(coin)
        if not dq:
            return []
        now_ms = int(time.time() * 1000)
        cutoff = now_ms - lookback_ms
        return [e for e in dq if e["time"] >= cutoff]

    def get_trades_window(self, coin: str, lookback_ms: int) -> list[dict]:
        dq = self.trades.get(coin)
        if not dq:
            return []
        now_ms = int(time.time() * 1000)
        cutoff = now_ms - lookback_ms
        return [e for e in dq if e["time"] >= cutoff]

    def get_snapshots_window(self, coin: str, lookback_ms: int) -> list[dict]:
        """Return snapshots where time (epoch seconds float) is within lookback_ms."""
        dq = self.asset_snapshots.get(coin)
        if not dq:
            return []
        now_ms = time.time() * 1000
        cutoff_s = (now_ms - lookback_ms) / 1000.0
        return [e for e in dq if e["time"] >= cutoff_s]

    def get_liquidations_window(self, coin: Optional[str], lookback_ms: int) -> list[dict]:
        if not self.liquidations:
            return []
        now_ms = int(time.time() * 1000)
        cutoff = now_ms - lookback_ms
        entries = [e for e in self.liquidations if e["time"] >= cutoff]
        if coin:
            entries = [e for e in entries if e["coin"] == coin]
        return entries

    # ------------------------------------------------------------------
    # Derived aggregations
    # ------------------------------------------------------------------

    def get_volume_summary(self, coin: str, lookback_ms: int) -> dict:
        """
        Returns {buy_volume, sell_volume, total_volume, cvd, trade_count}.
        Volume = sum(px * sz) per side. CVD = buy_volume - sell_volume.
        """
        trades = self.get_trades_window(coin, lookback_ms)
        buy_vol = 0.0
        sell_vol = 0.0
        for t in trades:
            notional = t["px"] * t["sz"]
            if t["side"] == "B":
                buy_vol += notional
            else:
                sell_vol += notional
        return {
            "buy_volume": buy_vol,
            "sell_volume": sell_vol,
            "total_volume": buy_vol + sell_vol,
            "cvd": buy_vol - sell_vol,
            "trade_count": len(trades),
        }

    def get_oi_change(self, coin: str, lookback_ms: int) -> dict:
        """
        Returns {oi_start, oi_end, oi_delta, oi_pct_change} from snapshots.
        """
        snaps = self.get_snapshots_window(coin, lookback_ms)
        if len(snaps) < 2:
            return {"oi_start": 0.0, "oi_end": 0.0, "oi_delta": 0.0, "oi_pct_change": 0.0}
        oi_start = snaps[0]["oi"]
        oi_end = snaps[-1]["oi"]
        oi_delta = oi_end - oi_start
        oi_pct_change = (oi_delta / oi_start * 100) if oi_start != 0 else 0.0
        return {
            "oi_start": oi_start,
            "oi_end": oi_end,
            "oi_delta": oi_delta,
            "oi_pct_change": oi_pct_change,
        }

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    def counts(self) -> dict:
        """Return current entry counts for logging / health checks."""
        return {
            "funding": {coin: len(dq) for coin, dq in self.funding_history.items()},
            "trades": {coin: len(dq) for coin, dq in self.trades.items()},
            "snapshots": {coin: len(dq) for coin, dq in self.asset_snapshots.items()},
            "liquidations": len(self.liquidations),
        }
