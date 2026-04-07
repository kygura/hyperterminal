"""
Shared pytest fixtures for the HyperTrade API tests.
Each test function gets its own isolated SQLite database to prevent locking.
"""
import sqlite3
import os
import pytest


# ─── Database fixture (function-scoped for isolation) ────────────────────────

@pytest.fixture()
def tmp_db(tmp_path):
    """A fresh SQLite database for each test function."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        PRAGMA journal_mode=WAL;

        CREATE TABLE IF NOT EXISTS trade_candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            asset TEXT NOT NULL,
            direction TEXT NOT NULL,
            conviction TEXT NOT NULL,
            regime TEXT,
            signals_json TEXT DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS news_articles (
            id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            title TEXT NOT NULL,
            url TEXT UNIQUE NOT NULL,
            published_at TEXT,
            summary TEXT,
            sentiment TEXT DEFAULT 'neutral',
            confidence REAL DEFAULT 0.5,
            impact TEXT DEFAULT 'low',
            affected_assets TEXT DEFAULT '[]',
            reasoning TEXT,
            processed_at TEXT
        );

        CREATE TABLE IF NOT EXISTS news_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            url TEXT UNIQUE NOT NULL,
            enabled INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now'))
        );

        CREATE TABLE IF NOT EXISTS signal_article_links (
            signal_id INTEGER,
            article_id TEXT,
            PRIMARY KEY (signal_id, article_id)
        );

        CREATE TABLE IF NOT EXISTS portfolio_branches (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            color TEXT DEFAULT '#2dd4bf',
            is_main INTEGER DEFAULT 0,
            parent_id TEXT,
            fork_date TEXT NOT NULL,
            balance REAL DEFAULT 10000,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS branch_positions (
            id TEXT PRIMARY KEY,
            branch_id TEXT NOT NULL,
            asset TEXT NOT NULL,
            direction TEXT NOT NULL,
            mode TEXT DEFAULT 'Cross',
            leverage REAL DEFAULT 1,
            margin REAL NOT NULL,
            entry_date TEXT NOT NULL,
            entry_price REAL NOT NULL,
            exit_date TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(branch_id) REFERENCES portfolio_branches(id)
        );
    """)
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture()
def tmp_config_root(tmp_path):
    """A temporary config directory with global.yaml and signals/ subdir."""
    import yaml
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "signals").mkdir()
    with open(cfg / "global.yaml", "w") as f:
        yaml.dump({"min_conviction": "MEDIUM", "max_signals_per_day": 10}, f)
    with open(cfg / "signals" / "funding_rate.yaml", "w") as f:
        yaml.dump({"threshold_sigma": 2.0, "lookback_hours": 8}, f)
    return cfg
