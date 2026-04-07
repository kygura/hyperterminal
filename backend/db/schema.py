"""
SQLite schema definitions and initialization.

Tables:
  funding_rates  — per-asset funding rate snapshots (from HL, Bybit, etc.)
  open_interest  — per-asset OI readings
  spot_volume    — HL WebSocket-derived spot/futures trade volume aggregations
  ohlcv          — hourly OHLCV candles from Bybit (VWAP/TWAP computed on insert)
  trade_candidates — output of confluence engine (historical log)
"""

from __future__ import annotations

DDL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS funding_rates (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          INTEGER NOT NULL,       -- epoch ms
    asset       TEXT    NOT NULL,
    source      TEXT    NOT NULL,       -- 'hyperliquid', 'bybit', etc.
    rate        REAL    NOT NULL,       -- funding rate (decimal, e.g. 0.0001)
    predicted   REAL                    -- predicted next rate if available
);
CREATE INDEX IF NOT EXISTS idx_fr_asset_ts ON funding_rates(asset, ts DESC);

CREATE TABLE IF NOT EXISTS open_interest (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          INTEGER NOT NULL,
    asset       TEXT    NOT NULL,
    source      TEXT    NOT NULL,
    oi          REAL    NOT NULL,       -- open interest in USD or coin units
    oi_change_pct REAL                  -- % change vs previous reading
);
CREATE INDEX IF NOT EXISTS idx_oi_asset_ts ON open_interest(asset, ts DESC);

CREATE TABLE IF NOT EXISTS volume_snapshots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          INTEGER NOT NULL,
    asset       TEXT    NOT NULL,
    source      TEXT    NOT NULL,
    buy_volume  REAL    NOT NULL DEFAULT 0,
    sell_volume REAL    NOT NULL DEFAULT 0,
    spot_volume REAL    NOT NULL DEFAULT 0,  -- spot notional (from Bybit spot or CoinGecko)
    futures_volume REAL NOT NULL DEFAULT 0   -- futures notional
);
CREATE INDEX IF NOT EXISTS idx_vol_asset_ts ON volume_snapshots(asset, ts DESC);

CREATE TABLE IF NOT EXISTS ohlcv (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          INTEGER NOT NULL,       -- candle open time (epoch ms)
    asset       TEXT    NOT NULL,
    source      TEXT    NOT NULL,       -- 'bybit'
    timeframe   TEXT    NOT NULL DEFAULT '1h',
    open        REAL    NOT NULL,
    high        REAL    NOT NULL,
    low         REAL    NOT NULL,
    close       REAL    NOT NULL,
    volume      REAL    NOT NULL,
    vwap        REAL,                   -- computed on insert
    twap        REAL                    -- time-weighted avg price over session
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_ohlcv_asset_ts ON ohlcv(asset, source, timeframe, ts);
CREATE INDEX IF NOT EXISTS idx_ohlcv_asset_ts2 ON ohlcv(asset, ts DESC);

CREATE TABLE IF NOT EXISTS asset_snapshots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          INTEGER NOT NULL,       -- epoch ms
    asset       TEXT    NOT NULL,
    source      TEXT    NOT NULL,
    mark_px     REAL,
    oracle_px   REAL,
    funding     REAL,
    oi          REAL,
    premium     REAL
);
CREATE INDEX IF NOT EXISTS idx_snap_asset_ts ON asset_snapshots(asset, ts DESC);

CREATE TABLE IF NOT EXISTS trade_candidates (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          INTEGER NOT NULL,
    asset       TEXT    NOT NULL,
    direction   TEXT    NOT NULL,       -- 'LONG', 'SHORT'
    regime      TEXT    NOT NULL,
    conviction  TEXT    NOT NULL,       -- 'HIGH', 'MEDIUM', 'LOW'
    signal_count INTEGER NOT NULL,
    signals_json TEXT   NOT NULL,       -- JSON array of contributing signal names
    price       REAL,
    vwap        REAL,
    alert_sent  INTEGER NOT NULL DEFAULT 0  -- 0=no, 1=yes
);
CREATE INDEX IF NOT EXISTS idx_tc_asset_ts ON trade_candidates(asset, ts DESC);

CREATE TABLE IF NOT EXISTS portfolio_branches (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    color           TEXT DEFAULT '#2dd4bf',
    is_main         INTEGER DEFAULT 0,
    parent_id       TEXT,
    fork_date       TEXT NOT NULL,
    initial_capital REAL DEFAULT 10000,
    balance         REAL DEFAULT 10000,
    source_wallet_id TEXT,
    source_type     TEXT,
    source_path     TEXT,
    source_mtime    REAL,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_branches_main_created ON portfolio_branches(is_main DESC, created_at ASC);

CREATE TABLE IF NOT EXISTS branch_positions (
    id          TEXT PRIMARY KEY,
    branch_id   TEXT NOT NULL REFERENCES portfolio_branches(id) ON DELETE CASCADE,
    asset       TEXT NOT NULL,
    direction   TEXT NOT NULL,
    mode        TEXT DEFAULT 'Cross',
    leverage    REAL DEFAULT 1,
    margin      REAL NOT NULL,
    entry_date  TEXT NOT NULL,
    entry_price REAL NOT NULL,
    exit_date   TEXT,
    exit_price  REAL,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_branch_positions_branch_entry ON branch_positions(branch_id, entry_date ASC);

CREATE TABLE IF NOT EXISTS branch_trades (
    id          TEXT PRIMARY KEY,
    branch_id   TEXT NOT NULL REFERENCES portfolio_branches(id) ON DELETE CASCADE,
    coin        TEXT NOT NULL,
    side        TEXT NOT NULL,
    size        REAL NOT NULL,
    leverage    REAL DEFAULT 1,
    margin      REAL,
    mode        TEXT DEFAULT 'Cross',
    entry_px    REAL NOT NULL,
    close_px    REAL,
    entry_date  TEXT NOT NULL,
    exit_date   TEXT,
    status      TEXT NOT NULL DEFAULT 'OPEN',
    notes       TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_branch_trades_branch_entry ON branch_trades(branch_id, entry_date ASC);
"""


def apply_schema(conn) -> None:
    """Apply DDL to an open sqlite3 connection."""
    conn.executescript(DDL)
    conn.commit()
