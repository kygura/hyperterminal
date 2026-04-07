"""
Tests for quant/api/signals.py
Covers: GET /api/signals/active, /history, /config, PUT /api/signals/config/{section}
"""
import sqlite3
import json
import os
import pytest
from datetime import datetime, timedelta, timezone
from fastapi import FastAPI
from fastapi.testclient import TestClient


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _utc_now_str() -> str:
    """SQLite-compatible UTC timestamp (no timezone offset)."""
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _utc_ago_str(hours: float = 0, minutes: float = 0) -> str:
    dt = datetime.utcnow() - timedelta(hours=hours, minutes=minutes)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


@pytest.fixture(autouse=True)
def patch_db(tmp_db, monkeypatch):
    monkeypatch.setenv("SIGNAL_DB_PATH", str(tmp_db))
    # _CANDIDATES is evaluated at module import time, so patch the list directly
    import api.signals as sig_mod
    monkeypatch.setattr(sig_mod, "_CANDIDATES", [tmp_db])


@pytest.fixture()
def client():
    from api.signals import router
    app = FastAPI()
    app.include_router(router)   # router already has prefix="/signals"
    return TestClient(app)


# ─── /active ─────────────────────────────────────────────────────────────────

class TestActiveSignals:
    def test_returns_empty_list_when_no_signals(self, client):
        resp = client.get("/signals/active")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_returns_latest_signal_per_asset_direction(self, client, tmp_db):
        conn = sqlite3.connect(str(tmp_db))
        rows = [
            (_utc_ago_str(minutes=5), "BTC", "LONG", "HIGH", "Squeeze", "{}"),
            (_utc_ago_str(minutes=1), "BTC", "LONG", "HIGH", "Squeeze", "{}"),  # newer
            (_utc_ago_str(minutes=10), "ETH", "SHORT", "MEDIUM", "Rally", "{}"),
        ]
        conn.executemany(
            "INSERT INTO trade_candidates (ts, asset, direction, conviction, regime, signals_json) VALUES (?,?,?,?,?,?)",
            rows,
        )
        conn.commit()
        conn.close()

        resp = client.get("/signals/active")
        assert resp.status_code == 200
        data = resp.json()
        pairs = {(r["asset"], r["direction"]) for r in data}
        assert ("BTC", "LONG") in pairs
        assert ("ETH", "SHORT") in pairs

    def test_returns_only_one_record_per_asset_direction(self, client, tmp_db):
        """The 'active' endpoint returns the LATEST signal per (asset, direction)."""
        conn = sqlite3.connect(str(tmp_db))
        rows = [
            (_utc_ago_str(minutes=5), "BTC", "LONG", "HIGH", "Old", "{}"),
            (_utc_ago_str(minutes=1), "BTC", "LONG", "HIGH", "New", "{}"),
        ]
        conn.executemany(
            "INSERT INTO trade_candidates (ts, asset, direction, conviction, regime, signals_json) VALUES (?,?,?,?,?,?)",
            rows,
        )
        conn.commit()
        conn.close()

        resp = client.get("/signals/active")
        data = resp.json()
        btc_longs = [r for r in data if r["asset"] == "BTC" and r["direction"] == "LONG"]
        assert len(btc_longs) == 1
        assert btc_longs[0]["regime"] == "New"

    def test_filter_by_asset(self, client, tmp_db):
        conn = sqlite3.connect(str(tmp_db))
        rows = [
            (_utc_now_str(), "BTC", "LONG", "HIGH", "X", "{}"),
            (_utc_now_str(), "SOL", "SHORT", "LOW", "Y", "{}"),
        ]
        conn.executemany(
            "INSERT INTO trade_candidates (ts, asset, direction, conviction, regime, signals_json) VALUES (?,?,?,?,?,?)",
            rows,
        )
        conn.commit()
        conn.close()

        resp = client.get("/signals/active?asset=BTC")
        assert resp.status_code == 200
        assert all(r["asset"] == "BTC" for r in resp.json())

    def test_signals_outside_24h_excluded(self, client, tmp_db):
        conn = sqlite3.connect(str(tmp_db))
        old_ts = _utc_ago_str(hours=25)
        conn.execute(
            "INSERT INTO trade_candidates (ts, asset, direction, conviction, regime, signals_json) VALUES (?,?,?,?,?,?)",
            (old_ts, "BTC", "LONG", "HIGH", "Old", "{}"),
        )
        conn.commit()
        conn.close()

        resp = client.get("/signals/active")
        assert resp.status_code == 200
        assert resp.json() == []


# ─── /history ─────────────────────────────────────────────────────────────────

class TestSignalHistory:
    def test_returns_all_signals_paginated(self, client, tmp_db):
        conn = sqlite3.connect(str(tmp_db))
        for i in range(5):
            conn.execute(
                "INSERT INTO trade_candidates (ts, asset, direction, conviction, regime, signals_json) VALUES (?,?,?,?,?,?)",
                (_utc_ago_str(minutes=i), "BTC", "LONG", "HIGH", "Test", "{}"),
            )
        conn.commit()
        conn.close()

        resp = client.get("/signals/history?limit=3&offset=0")
        assert resp.status_code == 200
        assert len(resp.json()) == 3

        resp2 = client.get("/signals/history?limit=3&offset=3")
        assert resp2.status_code == 200
        assert len(resp2.json()) == 2

    def test_filter_by_conviction(self, client, tmp_db):
        conn = sqlite3.connect(str(tmp_db))
        rows = [
            (_utc_now_str(), "BTC", "LONG", "HIGH", "X", "{}"),
            (_utc_now_str(), "ETH", "SHORT", "LOW", "Y", "{}"),
        ]
        conn.executemany(
            "INSERT INTO trade_candidates (ts, asset, direction, conviction, regime, signals_json) VALUES (?,?,?,?,?,?)",
            rows,
        )
        conn.commit()
        conn.close()

        resp = client.get("/signals/history?conviction=HIGH")
        assert resp.status_code == 200
        assert all(r["conviction"] == "HIGH" for r in resp.json())

    def test_signals_json_parsed_from_string(self, client, tmp_db):
        conn = sqlite3.connect(str(tmp_db))
        payload = json.dumps({"funding": -2.3, "oi_vol": 1.8})
        conn.execute(
            "INSERT INTO trade_candidates (ts, asset, direction, conviction, regime, signals_json) VALUES (?,?,?,?,?,?)",
            (_utc_now_str(), "BTC", "LONG", "HIGH", "Squeeze", payload),
        )
        conn.commit()
        conn.close()

        resp = client.get("/signals/history?limit=1")
        data = resp.json()
        assert isinstance(data[0]["signals_json"], dict)
        assert data[0]["signals_json"]["funding"] == pytest.approx(-2.3)

    def test_returns_empty_when_no_match(self, client):
        resp = client.get("/signals/history?asset=UNKNOWN_ASSET")
        assert resp.status_code == 200
        assert resp.json() == []


# ─── /config ──────────────────────────────────────────────────────────────────

class TestSignalConfig:
    def test_get_config_returns_dict(self, client, tmp_config_root):
        import api.signals as sig_mod
        original = sig_mod._get_config_root
        sig_mod._get_config_root = lambda: tmp_config_root
        try:
            resp = client.get("/signals/config")
            assert resp.status_code == 200
            data = resp.json()
            assert "global" in data
            assert data["global"]["min_conviction"] == "MEDIUM"
            assert "signals" in data
            assert "funding_rate" in data["signals"]
        finally:
            sig_mod._get_config_root = original

    def test_put_config_global_updates_yaml(self, client, tmp_config_root):
        import api.signals as sig_mod
        original = sig_mod._get_config_root
        sig_mod._get_config_root = lambda: tmp_config_root
        try:
            resp = client.put(
                "/signals/config/global",
                json={"data": {"min_conviction": "HIGH", "new_key": "value"}},
            )
            assert resp.status_code == 200
            result = resp.json()
            assert result["ok"] is True
            assert result["data"]["min_conviction"] == "HIGH"
            assert result["data"]["new_key"] == "value"
            # Verify persisted to disk
            import yaml
            with open(tmp_config_root / "global.yaml") as f:
                saved = yaml.safe_load(f)
            assert saved["min_conviction"] == "HIGH"
            assert saved["new_key"] == "value"
        finally:
            sig_mod._get_config_root = original

    def test_put_config_signal_subsection_merges(self, client, tmp_config_root):
        import api.signals as sig_mod
        original = sig_mod._get_config_root
        sig_mod._get_config_root = lambda: tmp_config_root
        try:
            resp = client.put(
                "/signals/config/signals/funding_rate",
                json={"data": {"threshold_sigma": 3.5}},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["data"]["threshold_sigma"] == pytest.approx(3.5)
            # Merge: original key should still be present
            assert "lookback_hours" in data["data"]
        finally:
            sig_mod._get_config_root = original

    def test_put_config_rejects_invalid_section(self, client, tmp_config_root):
        import api.signals as sig_mod
        original = sig_mod._get_config_root
        sig_mod._get_config_root = lambda: tmp_config_root
        try:
            resp = client.put(
                "/signals/config/unknown_section",
                json={"data": {"x": 1}},
            )
            assert resp.status_code == 400
        finally:
            sig_mod._get_config_root = original

    def test_get_config_no_config_dir(self, client):
        import api.signals as sig_mod
        original = sig_mod._get_config_root
        sig_mod._get_config_root = lambda: None
        try:
            resp = client.get("/signals/config")
            assert resp.status_code == 200
            assert "error" in resp.json()
        finally:
            sig_mod._get_config_root = original
