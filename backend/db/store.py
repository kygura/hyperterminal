"""
SQLite-backed DataStore — persistent replacement for the in-memory deque store.

Provides the same query interface as the original DataStore so signal modules
work without change, plus additional OHLCV/VWAP queries needed by v2 signals.

All timestamps are stored as epoch milliseconds (INTEGER).
All write methods are synchronous (called from the async event loop via
run_in_executor or directly — SQLite WAL mode handles concurrent reads fine
for our single-writer pattern).
"""

from __future__ import annotations

import json
import logging
import math
import sqlite3
import time
from pathlib import Path
from typing import Optional

from db.schema import apply_schema

logger = logging.getLogger(__name__)

_DEFAULT_DB_PATH = "data.db"


def _is_valid_number(v) -> bool:
    try:
        f = float(v)
        return math.isfinite(f)
    except (TypeError, ValueError):
        return False


class SQLiteDataStore:
    """
    Persistent, SQLite-backed data store.
    Implements the same public interface as the original in-memory DataStore
    so existing signal modules work without modification.
    """

    def __init__(self, db_path: str = _DEFAULT_DB_PATH) -> None:
        self._path = db_path
        self._conn: Optional[sqlite3.Connection] = None
        self._open()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _open(self) -> None:
        Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            self._path,
            check_same_thread=False,  # single event-loop writer
            isolation_level=None,     # autocommit; we manage transactions manually
        )
        self._conn.row_factory = sqlite3.Row
        apply_schema(self._conn)
        logger.info("SQLiteDataStore opened: %s", self._path)

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            logger.info("SQLiteDataStore closed")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _exec(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        return self._conn.execute(sql, params)

    def _q(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        return self._conn.execute(sql, params).fetchall()

    def _q1(self, sql: str, params: tuple = ()) -> Optional[sqlite3.Row]:
        return self._conn.execute(sql, params).fetchone()

    # ------------------------------------------------------------------
    # Write: funding
    # ------------------------------------------------------------------

    def add_funding(
        self,
        coin: str,
        rate: float,
        premium: float,
        ts: int,
        source: str = "hyperliquid",
    ) -> None:
        if not all(_is_valid_number(v) for v in (rate, premium, ts)):
            return
        self._exec(
            "INSERT INTO funding_rates(ts, asset, source, rate, predicted) VALUES(?,?,?,?,?)",
            (int(ts), coin, source, float(rate), float(premium)),
        )

    # ------------------------------------------------------------------
    # Write: asset snapshot (from HL REST metaAndAssetCtxs)
    # ------------------------------------------------------------------

    def add_snapshot(
        self,
        coin: str,
        funding: float,
        oi: float,
        mark_px: float,
        oracle_px: float,
        premium: float,
        source: str = "hyperliquid",
    ) -> None:
        for v in (funding, oi, mark_px, oracle_px, premium):
            if not _is_valid_number(v):
                return
        ts = int(time.time() * 1000)
        self._exec(
            """INSERT INTO asset_snapshots(ts, asset, source, mark_px, oracle_px, funding, oi, premium)
               VALUES(?,?,?,?,?,?,?,?)""",
            (ts, coin, source, float(mark_px), float(oracle_px), float(funding), float(oi), float(premium)),
        )
        # Mirror OI into open_interest table
        self._upsert_oi(coin, float(oi), ts, source)

    def _upsert_oi(self, coin: str, oi: float, ts: int, source: str) -> None:
        # Compute % change vs previous reading
        prev = self._q1(
            "SELECT oi FROM open_interest WHERE asset=? AND source=? ORDER BY ts DESC LIMIT 1",
            (coin, source),
        )
        if prev and prev["oi"] and prev["oi"] != 0:
            oi_change_pct = (oi - prev["oi"]) / prev["oi"] * 100
        else:
            oi_change_pct = 0.0
        self._exec(
            "INSERT INTO open_interest(ts, asset, source, oi, oi_change_pct) VALUES(?,?,?,?,?)",
            (ts, coin, source, oi, oi_change_pct),
        )

    # ------------------------------------------------------------------
    # Write: trades (from HL WebSocket)
    # ------------------------------------------------------------------

    def add_trade(self, coin: str, side: str, px: float, sz: float, ts: int) -> None:
        if not all(_is_valid_number(v) for v in (px, sz, ts)):
            return
        notional = float(px) * float(sz)
        buy_vol = notional if side == "B" else 0.0
        sell_vol = notional if side != "B" else 0.0
        # Aggregate into 1-minute buckets
        bucket_ms = (int(ts) // 60_000) * 60_000
        self._exec(
            """INSERT INTO volume_snapshots(ts, asset, source, buy_volume, sell_volume, futures_volume)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT DO NOTHING""",
            (bucket_ms, coin, "hyperliquid_ws", 0.0, 0.0, 0.0),
        )
        self._exec(
            """UPDATE volume_snapshots
               SET buy_volume = buy_volume + ?,
                   sell_volume = sell_volume + ?,
                   futures_volume = futures_volume + ?
               WHERE ts=? AND asset=? AND source=?""",
            (buy_vol, sell_vol, notional, bucket_ms, coin, "hyperliquid_ws"),
        )

    # ------------------------------------------------------------------
    # Write: liquidations (from HL WebSocket)
    # ------------------------------------------------------------------

    def add_liquidation(self, coin: str, side: str, px: float, sz: float, ts: int) -> None:
        # Liquidations not stored in v2 schema yet — log only
        logger.debug("Liquidation: %s %s px=%.4f sz=%.4f ts=%d", coin, side, px, sz, ts)

    # ------------------------------------------------------------------
    # Write: OHLCV (from Bybit REST)
    # ------------------------------------------------------------------

    def add_ohlcv(
        self,
        asset: str,
        ts: int,
        open_: float,
        high: float,
        low: float,
        close: float,
        volume: float,
        source: str = "bybit",
        timeframe: str = "1h",
    ) -> None:
        """Insert OHLCV candle. VWAP computed as (H+L+C)/3 approximation."""
        vwap = (high + low + close) / 3.0
        self._exec(
            """INSERT OR IGNORE INTO ohlcv(ts, asset, source, timeframe, open, high, low, close, volume, vwap)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (int(ts), asset, source, timeframe, open_, high, low, close, volume, vwap),
        )

    # ------------------------------------------------------------------
    # Write: OI (from Bybit REST)
    # ------------------------------------------------------------------

    def add_oi(
        self,
        coin: str,
        oi: float,
        ts: int,
        source: str = "bybit",
    ) -> None:
        self._upsert_oi(coin, float(oi), int(ts), source)

    # ------------------------------------------------------------------
    # Write: volume snapshot (from Bybit REST)
    # ------------------------------------------------------------------

    def add_volume_snapshot(
        self,
        coin: str,
        ts: int,
        futures_volume: float,
        spot_volume: float = 0.0,
        source: str = "bybit",
    ) -> None:
        self._exec(
            """INSERT OR IGNORE INTO volume_snapshots(ts, asset, source, buy_volume, sell_volume, spot_volume, futures_volume)
               VALUES(?,?,?,0,0,?,?)""",
            (int(ts), coin, source, spot_volume, futures_volume),
        )

    # ------------------------------------------------------------------
    # Write: trade candidate (from confluence engine)
    # ------------------------------------------------------------------

    def add_trade_candidate(
        self,
        asset: str,
        direction: str,
        regime: str,
        conviction: str,
        signal_names: list[str],
        price: Optional[float] = None,
        vwap: Optional[float] = None,
        alert_sent: bool = False,
    ) -> None:
        ts = int(time.time() * 1000)
        self._exec(
            """INSERT INTO trade_candidates(ts, asset, direction, regime, conviction, signal_count, signals_json, price, vwap, alert_sent)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (
                ts, asset, direction, regime, conviction,
                len(signal_names), json.dumps(signal_names),
                price, vwap, 1 if alert_sent else 0,
            ),
        )

    # ------------------------------------------------------------------
    # Read: funding window (mirrors original DataStore interface)
    # ------------------------------------------------------------------

    def get_funding_window(self, coin: str, lookback_ms: int) -> list[dict]:
        cutoff = int(time.time() * 1000) - lookback_ms
        rows = self._q(
            "SELECT ts, rate, predicted FROM funding_rates WHERE asset=? AND ts>=? ORDER BY ts ASC",
            (coin, cutoff),
        )
        return [{"time": r["ts"], "rate": r["rate"], "premium": r["predicted"] or 0.0} for r in rows]

    # ------------------------------------------------------------------
    # Read: snapshots window (mirrors original DataStore interface)
    # ------------------------------------------------------------------

    def get_snapshots_window(self, coin: str, lookback_ms: int) -> list[dict]:
        cutoff_ms = int(time.time() * 1000) - lookback_ms
        rows = self._q(
            """SELECT ts, mark_px, oracle_px, funding, oi, premium
               FROM asset_snapshots WHERE asset=? AND ts>=? ORDER BY ts ASC""",
            (coin, cutoff_ms),
        )
        return [
            {
                "time": r["ts"] / 1000.0,  # convert to seconds float (compat)
                "mark_px": r["mark_px"] or 0.0,
                "oracle_px": r["oracle_px"] or 0.0,
                "funding": r["funding"] or 0.0,
                "oi": r["oi"] or 0.0,
                "premium": r["premium"] or 0.0,
            }
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Read: volume summary (mirrors original DataStore interface)
    # ------------------------------------------------------------------

    def get_volume_summary(self, coin: str, lookback_ms: int) -> dict:
        cutoff = int(time.time() * 1000) - lookback_ms
        row = self._q1(
            """SELECT
                 SUM(buy_volume)     AS buy_volume,
                 SUM(sell_volume)    AS sell_volume,
                 SUM(futures_volume) AS futures_volume,
                 SUM(spot_volume)    AS spot_volume,
                 COUNT(*)            AS bucket_count
               FROM volume_snapshots
               WHERE asset=? AND ts>=?""",
            (coin, cutoff),
        )
        if not row or row["buy_volume"] is None:
            return {"buy_volume": 0.0, "sell_volume": 0.0, "total_volume": 0.0,
                    "cvd": 0.0, "trade_count": 0, "spot_volume": 0.0, "futures_volume": 0.0}
        buy = row["buy_volume"] or 0.0
        sell = row["sell_volume"] or 0.0
        spot = row["spot_volume"] or 0.0
        fut = row["futures_volume"] or 0.0
        return {
            "buy_volume": buy,
            "sell_volume": sell,
            "total_volume": buy + sell,
            "cvd": buy - sell,
            "trade_count": row["bucket_count"] or 0,
            "spot_volume": spot,
            "futures_volume": fut,
        }

    # ------------------------------------------------------------------
    # Read: OI change (mirrors original DataStore interface)
    # ------------------------------------------------------------------

    def get_oi_change(self, coin: str, lookback_ms: int) -> dict:
        cutoff = int(time.time() * 1000) - lookback_ms
        rows = self._q(
            """SELECT oi FROM open_interest WHERE asset=? AND ts>=? ORDER BY ts ASC LIMIT 2""",
            (coin, cutoff),
        )
        # Also try from asset_snapshots as fallback
        if len(rows) < 2:
            snap_rows = self._q(
                """SELECT oi FROM asset_snapshots WHERE asset=? AND ts>=? AND oi IS NOT NULL
                   ORDER BY ts ASC LIMIT 2""",
                (coin, cutoff),
            )
            rows = snap_rows
        if len(rows) < 2:
            return {"oi_start": 0.0, "oi_end": 0.0, "oi_delta": 0.0, "oi_pct_change": 0.0}
        oi_start = rows[0]["oi"]
        # Get last row
        last = self._q1(
            "SELECT oi FROM open_interest WHERE asset=? AND ts>=? ORDER BY ts DESC LIMIT 1",
            (coin, cutoff),
        ) or self._q1(
            "SELECT oi FROM asset_snapshots WHERE asset=? AND ts>=? AND oi IS NOT NULL ORDER BY ts DESC LIMIT 1",
            (coin, cutoff),
        )
        oi_end = last["oi"] if last else oi_start
        oi_delta = oi_end - oi_start
        oi_pct = (oi_delta / oi_start * 100) if oi_start != 0 else 0.0
        return {"oi_start": oi_start, "oi_end": oi_end, "oi_delta": oi_delta, "oi_pct_change": oi_pct}

    # ------------------------------------------------------------------
    # Read: trades window (mirrors original DataStore interface)
    # ------------------------------------------------------------------

    def get_trades_window(self, coin: str, lookback_ms: int) -> list[dict]:
        """
        Returns aggregated volume buckets shaped like trade records.
        CVD-style signals will use these buckets.
        """
        cutoff = int(time.time() * 1000) - lookback_ms
        rows = self._q(
            """SELECT ts, buy_volume, sell_volume, futures_volume
               FROM volume_snapshots WHERE asset=? AND ts>=? ORDER BY ts ASC""",
            (coin, cutoff),
        )
        result = []
        for r in rows:
            total = (r["buy_volume"] or 0.0) + (r["sell_volume"] or 0.0)
            if total > 0:
                # Reconstruct pseudo-trades: one buy bucket, one sell bucket
                if r["buy_volume"]:
                    result.append({"side": "B", "px": 1.0, "sz": r["buy_volume"], "time": r["ts"]})
                if r["sell_volume"]:
                    result.append({"side": "S", "px": 1.0, "sz": r["sell_volume"], "time": r["ts"]})
        return result

    # ------------------------------------------------------------------
    # Read: liquidations window (mirrors original DataStore interface)
    # ------------------------------------------------------------------

    def get_liquidations_window(self, coin: Optional[str], lookback_ms: int) -> list[dict]:
        return []  # Not stored in v2 schema

    # ------------------------------------------------------------------
    # Read: OHLCV (new — used by VWAP/Spot-Led signals)
    # ------------------------------------------------------------------

    def get_ohlcv_window(self, asset: str, lookback_ms: int, source: str = "bybit") -> list[dict]:
        cutoff = int(time.time() * 1000) - lookback_ms
        rows = self._q(
            """SELECT ts, open, high, low, close, volume, vwap
               FROM ohlcv WHERE asset=? AND source=? AND ts>=? ORDER BY ts ASC""",
            (asset, source, cutoff),
        )
        return [dict(r) for r in rows]

    def get_latest_ohlcv(self, asset: str, source: str = "bybit") -> Optional[dict]:
        row = self._q1(
            "SELECT * FROM ohlcv WHERE asset=? AND source=? ORDER BY ts DESC LIMIT 1",
            (asset, source),
        )
        return dict(row) if row else None

    # ------------------------------------------------------------------
    # Read: VWAP computation (session VWAP = current day UTC)
    # ------------------------------------------------------------------

    def get_session_vwap(self, asset: str) -> Optional[float]:
        """Compute VWAP for the current UTC session (today's candles)."""
        import datetime
        today_start = int(datetime.datetime.utcnow().replace(
            hour=0, minute=0, second=0, microsecond=0
        ).timestamp() * 1000)
        rows = self._q(
            """SELECT high, low, close, volume FROM ohlcv
               WHERE asset=? AND ts>=? AND volume>0 ORDER BY ts ASC""",
            (asset, today_start),
        )
        if not rows:
            return None
        cum_tp_vol = sum(((r["high"] + r["low"] + r["close"]) / 3.0) * r["volume"] for r in rows)
        cum_vol = sum(r["volume"] for r in rows)
        return cum_tp_vol / cum_vol if cum_vol > 0 else None

    def get_vwap_std(self, asset: str, lookback_ms: int) -> Optional[tuple[float, float]]:
        """Return (vwap, std_dev) of VWAP values over the lookback window."""
        import math as _math
        rows = self._q(
            """SELECT vwap FROM ohlcv WHERE asset=? AND ts>=? AND vwap IS NOT NULL ORDER BY ts ASC""",
            (asset, int(time.time() * 1000) - lookback_ms),
        )
        if len(rows) < 2:
            return None
        vals = [r["vwap"] for r in rows]
        mean = sum(vals) / len(vals)
        variance = sum((v - mean) ** 2 for v in vals) / len(vals)
        return mean, _math.sqrt(variance)

    # ------------------------------------------------------------------
    # Read: spot volume rolling avg (used by Spot-Led Flow signal)
    # ------------------------------------------------------------------

    def get_spot_volume_rolling_avg(self, asset: str, lookback_ms: int) -> float:
        """Average per-bucket spot_volume (from Bybit kline volume) over lookback."""
        cutoff = int(time.time() * 1000) - lookback_ms
        row = self._q1(
            """SELECT AVG(spot_volume) AS avg_vol, COUNT(*) AS cnt
               FROM volume_snapshots
               WHERE asset=? AND ts>=? AND spot_volume > 0""",
            (asset, cutoff),
        )
        if not row or row["avg_vol"] is None:
            return 0.0
        return float(row["avg_vol"])

    # ------------------------------------------------------------------
    # Read: trade candidate history
    # ------------------------------------------------------------------

    def get_recent_candidates(self, asset: Optional[str] = None, limit: int = 50) -> list[dict]:
        if asset:
            rows = self._q(
                "SELECT * FROM trade_candidates WHERE asset=? ORDER BY ts DESC LIMIT ?",
                (asset, limit),
            )
        else:
            rows = self._q(
                "SELECT * FROM trade_candidates ORDER BY ts DESC LIMIT ?",
                (limit,),
            )
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Diagnostics (mirrors original DataStore interface)
    # ------------------------------------------------------------------

    def counts(self) -> dict:
        result = {}
        for table in ("funding_rates", "open_interest", "volume_snapshots", "ohlcv", "asset_snapshots", "trade_candidates"):
            row = self._q1(f"SELECT COUNT(*) AS n FROM {table}")
            result[table] = row["n"] if row else 0
        return result
