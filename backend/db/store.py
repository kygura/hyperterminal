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
        db_file = Path(self._path)
        db_file.parent.mkdir(parents=True, exist_ok=True)

        def _connect() -> sqlite3.Connection:
            conn = sqlite3.connect(
                self._path,
                check_same_thread=False,
                isolation_level=None,
            )
            conn.row_factory = sqlite3.Row
            return conn

        self._conn = _connect()
        try:
            apply_schema(self._conn)
        except sqlite3.IntegrityError as exc:
            logger.warning("Rebuilding SQLite DB %s after schema migration failure: %s", self._path, exc)
            self._conn.close()
            if db_file.exists():
                db_file.unlink()
            self._conn = _connect()
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

    def _next_snapshot_ts(self, coin: str, source: str) -> int:
        """Keep per-source snapshot timestamps monotonic across sub-ms inserts."""
        ts = int(time.time() * 1000)
        last = self._q1(
            """SELECT ts FROM asset_snapshots
               WHERE asset=? AND source=?
               ORDER BY ts DESC, id DESC LIMIT 1""",
            (coin, source),
        )
        if last and last["ts"] is not None and ts <= int(last["ts"]):
            return int(last["ts"]) + 1
        return ts

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
            """INSERT INTO funding_rates(ts, asset, source, rate, predicted)
               VALUES(?,?,?,?,?)
               ON CONFLICT(asset, source, ts) DO UPDATE SET
                   rate = excluded.rate,
                   predicted = excluded.predicted""",
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
        ts = self._next_snapshot_ts(coin, source)
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
            """SELECT oi FROM open_interest
               WHERE asset=? AND source=? AND ts < ?
               ORDER BY ts DESC LIMIT 1""",
            (coin, source, ts),
        )
        if prev and prev["oi"] and prev["oi"] != 0:
            oi_change_pct = (oi - prev["oi"]) / prev["oi"] * 100
        else:
            oi_change_pct = 0.0
        self._exec(
            """INSERT INTO open_interest(ts, asset, source, oi, oi_change_pct)
               VALUES(?,?,?,?,?)
               ON CONFLICT(asset, source, ts) DO UPDATE SET
                   oi = excluded.oi,
                   oi_change_pct = excluded.oi_change_pct""",
            (ts, coin, source, oi, oi_change_pct),
        )

    # ------------------------------------------------------------------
    # Write: trades (from HL WebSocket)
    # ------------------------------------------------------------------

    def add_trade(self, coin: str, side: str, px: float, sz: float, ts: int) -> None:
        if not all(_is_valid_number(v) for v in (px, sz, ts)):
            return
        notional = float(px) * float(sz)
        self.add_trade_tick(coin, side, px, sz, ts)
        buy_vol = notional if side == "B" else 0.0
        sell_vol = notional if side != "B" else 0.0
        # Aggregate into 1-minute buckets
        bucket_ms = (int(ts) // 60_000) * 60_000
        self._exec(
            """INSERT INTO volume_snapshots(ts, asset, source, buy_volume, sell_volume, futures_volume)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT(asset, source, ts) DO UPDATE SET
                   buy_volume = volume_snapshots.buy_volume + excluded.buy_volume,
                   sell_volume = volume_snapshots.sell_volume + excluded.sell_volume,
                   futures_volume = volume_snapshots.futures_volume + excluded.futures_volume""",
            (bucket_ms, coin, "hyperliquid_ws", buy_vol, sell_vol, notional),
        )

    def add_trade_tick(self, coin: str, side: str, px: float, sz: float, ts: int) -> None:
        if not all(_is_valid_number(v) for v in (px, sz, ts)):
            return
        self._exec(
            """INSERT INTO trade_ticks(ts, asset, side, px, sz, notional)
               VALUES(?,?,?,?,?,?)""",
            (int(ts), coin, side, float(px), float(sz), float(px) * float(sz)),
        )

    # ------------------------------------------------------------------
    # Write: liquidations (from HL WebSocket)
    # ------------------------------------------------------------------

    def add_liquidation(self, coin: str, side: str, px: float, sz: float, ts: int) -> None:
        if not all(_is_valid_number(v) for v in (px, sz, ts)):
            return
        self._exec(
            """INSERT INTO liquidations(ts, asset, side, px, sz, notional)
               VALUES(?,?,?,?,?,?)""",
            (int(ts), coin, side, float(px), float(sz), float(px) * float(sz)),
        )

    def add_orderbook_snapshot(
        self,
        coin: str,
        ts: int,
        bids: list[dict],
        asks: list[dict],
        depth_levels: int = 10,
    ) -> None:
        if not _is_valid_number(ts):
            return
        bid_levels = self._normalize_book_levels(bids, depth_levels)
        ask_levels = self._normalize_book_levels(asks, depth_levels)
        if not bid_levels or not ask_levels:
            return

        best_bid = bid_levels[0]["px"]
        best_ask = ask_levels[0]["px"]
        if best_ask <= 0 or best_bid <= 0 or best_ask < best_bid:
            return

        bid_total = sum(level["sz"] for level in bid_levels)
        ask_total = sum(level["sz"] for level in ask_levels)
        total_depth = bid_total + ask_total
        if total_depth <= 0:
            return

        self._exec(
            """INSERT INTO orderbook_snapshots(
                   ts, asset, bid_depth_json, ask_depth_json, bid_total, ask_total,
                   imbalance_ratio, spread, mid_px
               ) VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                int(ts),
                coin,
                json.dumps(bid_levels),
                json.dumps(ask_levels),
                bid_total,
                ask_total,
                bid_total / total_depth,
                best_ask - best_bid,
                (best_ask + best_bid) / 2.0,
            ),
        )

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
            """INSERT INTO ohlcv(ts, asset, source, timeframe, open, high, low, close, volume, vwap)
               VALUES(?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(asset, source, timeframe, ts) DO UPDATE SET
                   open = excluded.open,
                   high = excluded.high,
                   low = excluded.low,
                   close = excluded.close,
                   volume = excluded.volume,
                   vwap = excluded.vwap""",
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
            """INSERT INTO volume_snapshots(ts, asset, source, buy_volume, sell_volume, spot_volume, futures_volume)
               VALUES(?,?,?,0,0,?,?)
               ON CONFLICT(asset, source, ts) DO UPDATE SET
                   spot_volume = excluded.spot_volume,
                   futures_volume = excluded.futures_volume""",
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
        delivery_bucket: Optional[str] = None,
        dedupe_key: Optional[str] = None,
    ) -> bool:
        ts = int(time.time() * 1000)
        cursor = self._exec(
            """INSERT INTO trade_candidates(
                   ts, asset, direction, regime, conviction, signal_count, signals_json,
                   price, vwap, alert_sent, delivery_bucket, dedupe_key
               )
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(delivery_bucket, dedupe_key) DO NOTHING""",
            (
                ts, asset, direction, regime, conviction,
                len(signal_names), json.dumps(signal_names),
                price, vwap, 1 if alert_sent else 0,
                delivery_bucket, dedupe_key,
            ),
        )
        return cursor.rowcount > 0

    def get_latest_timestamps(self, asset: str) -> dict[str, Optional[int]]:
        table_specs = {
            "asset_snapshots": ("asset_snapshots", "asset"),
            "funding_rates": ("funding_rates", "asset"),
            "open_interest": ("open_interest", "asset"),
            "volume_snapshots": ("volume_snapshots", "asset"),
            "ohlcv": ("ohlcv", "asset"),
            "trade_ticks": ("trade_ticks", "asset"),
            "orderbook_snapshots": ("orderbook_snapshots", "asset"),
            "liquidations": ("liquidations", "asset"),
        }
        latest: dict[str, Optional[int]] = {}
        for key, (table, field) in table_specs.items():
            row = self._q1(
                f"SELECT MAX(ts) AS ts FROM {table} WHERE {field}=?",
                (asset,),
            )
            latest[key] = int(row["ts"]) if row and row["ts"] is not None else None
        return latest

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
               FROM asset_snapshots WHERE asset=? AND ts>=? ORDER BY ts ASC, id ASC""",
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
            """SELECT oi FROM open_interest WHERE asset=? AND ts>=? ORDER BY ts ASC, id ASC LIMIT 2""",
            (coin, cutoff),
        )
        # Also try from asset_snapshots as fallback
        if len(rows) < 2:
            snap_rows = self._q(
                """SELECT oi FROM asset_snapshots WHERE asset=? AND ts>=? AND oi IS NOT NULL
                   ORDER BY ts ASC, id ASC LIMIT 2""",
                (coin, cutoff),
            )
            rows = snap_rows
        if len(rows) < 2:
            return {"oi_start": 0.0, "oi_end": 0.0, "oi_delta": 0.0, "oi_pct_change": 0.0}
        oi_start = rows[0]["oi"]
        # Get last row
        last = self._q1(
            "SELECT oi FROM open_interest WHERE asset=? AND ts>=? ORDER BY ts DESC, id DESC LIMIT 1",
            (coin, cutoff),
        ) or self._q1(
            "SELECT oi FROM asset_snapshots WHERE asset=? AND ts>=? AND oi IS NOT NULL ORDER BY ts DESC, id DESC LIMIT 1",
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
        tick_rows = self._q(
            """SELECT ts, side, px, sz, notional
               FROM trade_ticks WHERE asset=? AND ts>=? ORDER BY ts ASC""",
            (coin, cutoff),
        )
        if tick_rows:
            return [
                {
                    "side": row["side"],
                    "px": row["px"],
                    "sz": row["sz"],
                    "time": row["ts"],
                    "ts": row["ts"],
                    "notional": row["notional"],
                }
                for row in tick_rows
            ]

        rows = self._q(
            """SELECT ts, buy_volume, sell_volume, futures_volume
               FROM volume_snapshots WHERE asset=? AND ts>=? ORDER BY ts ASC""",
            (coin, cutoff),
        )
        result = []
        for r in rows:
            total = (r["buy_volume"] or 0.0) + (r["sell_volume"] or 0.0)
            if total > 0:
                # Preserve backward compatibility when only aggregated volume exists.
                if r["buy_volume"]:
                    result.append({"side": "B", "px": 1.0, "sz": r["buy_volume"], "time": r["ts"], "ts": r["ts"]})
                if r["sell_volume"]:
                    result.append({"side": "S", "px": 1.0, "sz": r["sell_volume"], "time": r["ts"], "ts": r["ts"]})
        return result

    def get_trade_ticks(self, coin: str, lookback_ms: int) -> list[dict]:
        cutoff = int(time.time() * 1000) - lookback_ms
        rows = self._q(
            """SELECT ts, side, px, sz, notional
               FROM trade_ticks WHERE asset=? AND ts>=? ORDER BY ts ASC""",
            (coin, cutoff),
        )
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Read: liquidations window (mirrors original DataStore interface)
    # ------------------------------------------------------------------

    def get_liquidations_window(self, coin: Optional[str], lookback_ms: int) -> list[dict]:
        cutoff = int(time.time() * 1000) - lookback_ms
        if coin:
            rows = self._q(
                """SELECT ts, asset, side, px, sz, notional
                   FROM liquidations WHERE asset=? AND ts>=? ORDER BY ts ASC""",
                (coin, cutoff),
            )
        else:
            rows = self._q(
                """SELECT ts, asset, side, px, sz, notional
                   FROM liquidations WHERE ts>=? ORDER BY ts ASC""",
                (cutoff,),
            )
        return [dict(r) for r in rows]

    def get_liquidation_summary(self, coin: str, lookback_ms: int) -> dict:
        cutoff = int(time.time() * 1000) - lookback_ms
        row = self._q1(
            """SELECT
                 SUM(CASE WHEN side='B' THEN notional ELSE 0 END) AS long_notional,
                 SUM(CASE WHEN side='S' THEN notional ELSE 0 END) AS short_notional,
                 SUM(CASE WHEN side='B' THEN 1 ELSE 0 END) AS long_count,
                 SUM(CASE WHEN side='S' THEN 1 ELSE 0 END) AS short_count
               FROM liquidations
               WHERE asset=? AND ts>=?""",
            (coin, cutoff),
        )
        if not row:
            return {
                "long_notional": 0.0,
                "short_notional": 0.0,
                "total_notional": 0.0,
                "long_count": 0,
                "short_count": 0,
                "total_count": 0,
            }
        long_notional = float(row["long_notional"] or 0.0)
        short_notional = float(row["short_notional"] or 0.0)
        long_count = int(row["long_count"] or 0)
        short_count = int(row["short_count"] or 0)
        return {
            "long_notional": long_notional,
            "short_notional": short_notional,
            "total_notional": long_notional + short_notional,
            "long_count": long_count,
            "short_count": short_count,
            "total_count": long_count + short_count,
        }

    def get_orderbook_imbalance_window(self, asset: str, lookback_ms: int) -> list[dict]:
        cutoff = int(time.time() * 1000) - lookback_ms
        rows = self._q(
            """SELECT ts, bid_total, ask_total, imbalance_ratio, spread, mid_px
               FROM orderbook_snapshots WHERE asset=? AND ts>=? ORDER BY ts ASC""",
            (asset, cutoff),
        )
        return [dict(r) for r in rows]

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
        """
        Average spot volume over the lookback window.

        Only rows with explicit spot volume are included. Futures-only buckets
        are ignored so the result cannot be polluted by non-spot flow.
        """
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

    def prune_old_ticks(self, max_age_ms: int) -> dict[str, int]:
        cutoff = int(time.time() * 1000) - max_age_ms
        deleted_trade_ticks = self._exec(
            "DELETE FROM trade_ticks WHERE ts < ?",
            (cutoff,),
        ).rowcount
        deleted_orderbook = self._exec(
            "DELETE FROM orderbook_snapshots WHERE ts < ?",
            (cutoff,),
        ).rowcount
        deleted_liquidations = self._exec(
            "DELETE FROM liquidations WHERE ts < ?",
            (cutoff,),
        ).rowcount
        return {
            "trade_ticks": max(deleted_trade_ticks, 0),
            "orderbook_snapshots": max(deleted_orderbook, 0),
            "liquidations": max(deleted_liquidations, 0),
        }

    # ------------------------------------------------------------------
    # Diagnostics (mirrors original DataStore interface)
    # ------------------------------------------------------------------

    def counts(self) -> dict:
        result = {}
        for table in (
            "funding_rates",
            "open_interest",
            "volume_snapshots",
            "trade_ticks",
            "liquidations",
            "orderbook_snapshots",
            "ohlcv",
            "asset_snapshots",
            "trade_candidates",
        ):
            row = self._q1(f"SELECT COUNT(*) AS n FROM {table}")
            result[table] = row["n"] if row else 0
        return result

    def _normalize_book_levels(self, levels: list[dict], depth_levels: int) -> list[dict]:
        normalized: list[dict] = []
        for level in levels[:depth_levels]:
            try:
                px = float(level.get("px", 0))
                sz = float(level.get("sz", 0))
                n_orders = int(level.get("n", 0))
            except (AttributeError, TypeError, ValueError):
                continue
            if px <= 0 or sz <= 0:
                continue
            normalized.append({"px": px, "sz": sz, "n": n_orders})
        return normalized
