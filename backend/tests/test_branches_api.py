"""
Tests for quant/api/branches.py
Covers: GET /branches, POST /fork, PUT /{id}, DELETE /{id},
        POST /{id}/positions, PUT /{id}/positions/{pos_id}, DELETE /{id}/positions/{pos_id}
"""
import sqlite3
import os
import pytest
from datetime import datetime, timezone
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pathlib import Path


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def patch_branches_db(tmp_db, tmp_path, monkeypatch):
    monkeypatch.setenv("BRANCHES_DB_PATH", str(tmp_db))
    branches_dir = tmp_path / "branches"
    branches_dir.mkdir()
    monkeypatch.setenv("BRANCHES_YAML_DIR", str(branches_dir))


@pytest.fixture()
def client():
    from api.branches import router
    app = FastAPI()
    app.include_router(router)   # router already has prefix="/branches"
    return TestClient(app)


def _seed_main(tmp_db):
    conn = sqlite3.connect(str(tmp_db))
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        "INSERT INTO portfolio_branches (id, name, color, is_main, parent_id, fork_date, balance, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
        ("main", "Main Portfolio", "#2dd4bf", 1, None, now, 10000.0, now, now),
    )
    conn.commit()
    conn.close()


def _write_price_csv(directory: Path, filename: str, rows: list[tuple[str, float, float, float, float, float]]):
    filepath = directory / filename
    with filepath.open("w", encoding="utf-8") as handle:
        handle.write("date,open,high,low,close,volume\n")
        for date, open_price, high_price, low_price, close_price, volume in rows:
            handle.write(
                f"{date},{open_price},{high_price},{low_price},{close_price},{volume}\n"
            )


def _write_price_config(path: Path, cutoff_date: str):
    path.write_text(
        "\n".join(
            [
                "price_history:",
                f"  cutoff_date: \"{cutoff_date}\"",
                "  hydration_timeframes:",
                "    - 1h",
                "    - 4h",
                "    - 1d",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def _write_branch_yaml(path: Path, body: str):
    import textwrap

    path.write_text(textwrap.dedent(body).strip() + "\n", encoding="utf-8")


# ─── GET /branches ────────────────────────────────────────────────────────────

class TestListBranches:
    def test_empty_returns_list(self, client):
        resp = client.get("/branches")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_returns_seeded_main(self, client, tmp_db):
        _seed_main(tmp_db)
        resp = client.get("/branches")
        data = resp.json()
        assert len(data) >= 1
        main = next((b for b in data if b["id"] == "main"), None)
        assert main is not None

    def test_each_branch_has_positions_key(self, client, tmp_db):
        _seed_main(tmp_db)
        for branch in client.get("/branches").json():
            assert "positions" in branch
            assert isinstance(branch["positions"], list)

    def test_main_branch_returned_first(self, client, tmp_db):
        _seed_main(tmp_db)
        # Fork a child
        client.post("/branches/fork", json={"name": "Child", "balance": 5000.0, "parent_id": "main"})
        data = client.get("/branches").json()
        assert data[0]["is_main"] == 1


# ─── POST /branches/fork ──────────────────────────────────────────────────────

class TestForkBranch:
    def test_creates_branch_with_correct_fields(self, client, tmp_db):
        _seed_main(tmp_db)
        resp = client.post(
            "/branches/fork",
            json={"name": "Bull Run", "color": "#ff0000", "balance": 8000.0, "parent_id": "main"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Bull Run"
        assert data["balance"] == pytest.approx(8000.0)
        assert data["is_main"] == 0
        assert data["parent_id"] == "main"

    def test_fork_without_parent_has_null_parent_id(self, client):
        resp = client.post("/branches/fork", json={"name": "Standalone", "balance": 5000.0})
        assert resp.status_code == 200
        assert resp.json()["parent_id"] is None

    def test_forked_branch_appears_in_list(self, client, tmp_db):
        _seed_main(tmp_db)
        fork = client.post("/branches/fork", json={"name": "Strategy A", "balance": 7000.0, "parent_id": "main"}).json()
        branches = client.get("/branches").json()
        assert any(b["id"] == fork["id"] for b in branches)

    def test_fork_copies_open_positions_from_parent(self, client, tmp_db):
        _seed_main(tmp_db)
        # Add an open position to main
        client.post(
            "/branches/main/positions",
            json={
                "asset": "BTC", "direction": "Long", "mode": "Cross",
                "leverage": 10.0, "margin": 500.0,
                "entry_date": "2024-01-01", "entry_price": 40000.0,
            },
        )
        # Fork from main
        fork = client.post(
            "/branches/fork",
            json={"name": "Fork A", "balance": 10000.0, "parent_id": "main"},
        ).json()

        branches = client.get("/branches").json()
        fork_branch = next(b for b in branches if b["id"] == fork["id"])
        # The fork should inherit the open position
        assert len(fork_branch["positions"]) >= 1


# ─── PUT /branches/{id} ───────────────────────────────────────────────────────

class TestUpdateBranch:
    def test_rename_branch(self, client, tmp_db):
        _seed_main(tmp_db)
        resp = client.put("/branches/main", json={"name": "Renamed"})
        assert resp.status_code == 200
        assert resp.json()["name"] == "Renamed"

    def test_update_balance(self, client, tmp_db):
        _seed_main(tmp_db)
        resp = client.put("/branches/main", json={"balance": 20000.0})
        assert resp.status_code == 200
        assert resp.json()["balance"] == pytest.approx(20000.0)

    def test_update_nonexistent_returns_404(self, client):
        resp = client.put("/branches/no-such-id", json={"name": "X"})
        assert resp.status_code == 404

    def test_partial_update_preserves_other_fields(self, client, tmp_db):
        _seed_main(tmp_db)
        client.put("/branches/main", json={"name": "New Name"})
        branch = next(b for b in client.get("/branches").json() if b["id"] == "main")
        # balance should still be the original 10000
        assert branch["balance"] == pytest.approx(10000.0)


# ─── DELETE /branches/{id} ────────────────────────────────────────────────────

class TestDeleteBranch:
    def test_delete_non_main_branch(self, client, tmp_db):
        _seed_main(tmp_db)
        fork = client.post("/branches/fork", json={"name": "Delete Me", "balance": 1000.0, "parent_id": "main"}).json()
        fork_id = fork["id"]

        assert client.delete(f"/branches/{fork_id}").status_code == 200

        branches = client.get("/branches").json()
        assert not any(b["id"] == fork_id for b in branches)

    def test_cannot_delete_main_branch(self, client, tmp_db):
        _seed_main(tmp_db)
        resp = client.delete("/branches/main")
        assert resp.status_code in (400, 403)

    def test_delete_nonexistent_returns_404(self, client):
        assert client.delete("/branches/ghost-id").status_code == 404


# ─── Positions ────────────────────────────────────────────────────────────────

class TestPositions:
    def test_add_position(self, client, tmp_db):
        _seed_main(tmp_db)
        resp = client.post(
            "/branches/main/positions",
            json={
                "asset": "ETH",
                "direction": "Short",
                "mode": "Isolated",
                "leverage": 5.0,
                "margin": 200.0,
                "entry_date": "2024-06-01",
                "entry_price": 3500.0,
            },
        )
        assert resp.status_code == 200
        pos = resp.json()
        assert pos["asset"] == "ETH"
        assert pos["direction"] == "Short"
        assert pos["mode"] == "Isolated"
        assert pos["margin"] == pytest.approx(200.0)

    def test_add_position_to_nonexistent_branch(self, client):
        resp = client.post(
            "/branches/ghost/positions",
            json={
                "asset": "BTC", "direction": "Long", "mode": "Cross",
                "leverage": 10.0, "margin": 500.0,
                "entry_date": "2024-01-01", "entry_price": 40000.0,
            },
        )
        assert resp.status_code == 404

    def test_position_appears_in_branch_list(self, client, tmp_db):
        _seed_main(tmp_db)
        client.post(
            "/branches/main/positions",
            json={
                "asset": "SOL", "direction": "Long", "mode": "Cross",
                "leverage": 5.0, "margin": 300.0,
                "entry_date": "2024-03-01", "entry_price": 150.0,
            },
        )
        main = next(b for b in client.get("/branches").json() if b["id"] == "main")
        assert len(main["positions"]) == 1
        assert main["positions"][0]["asset"] == "SOL"

    def test_update_position_exit_date(self, client, tmp_db):
        _seed_main(tmp_db)
        pos = client.post(
            "/branches/main/positions",
            json={
                "asset": "BTC", "direction": "Long", "mode": "Cross",
                "leverage": 10.0, "margin": 500.0,
                "entry_date": "2024-01-01", "entry_price": 40000.0,
            },
        ).json()

        resp = client.put(
            f"/branches/main/positions/{pos['id']}",
            json={"exit_date": "2024-04-01"},
        )
        assert resp.status_code == 200
        assert resp.json()["exit_date"] == "2024-04-01"

    def test_delete_position(self, client, tmp_db):
        _seed_main(tmp_db)
        pos = client.post(
            "/branches/main/positions",
            json={
                "asset": "HYPE", "direction": "Long", "mode": "Cross",
                "leverage": 3.0, "margin": 100.0,
                "entry_date": "2024-05-01", "entry_price": 20.0,
            },
        ).json()
        pos_id = pos["id"]

        assert client.delete(f"/branches/main/positions/{pos_id}").status_code == 200

        main = next(b for b in client.get("/branches").json() if b["id"] == "main")
        assert not any(p["id"] == pos_id for p in main["positions"])

    def test_delete_nonexistent_position_is_idempotent(self, client, tmp_db):
        # The delete endpoint does a DELETE WHERE ... without checking rows affected,
        # so deleting a non-existent position returns 200 (idempotent).
        _seed_main(tmp_db)
        resp = client.delete("/branches/main/positions/nonexistent-pos-id")
        assert resp.status_code == 200


class TestPriceHistory:
    def test_price_history_uses_csv_only_before_global_cutoff(self, client, tmp_path, monkeypatch):
        price_dir = tmp_path / "prices"
        price_dir.mkdir()
        config_path = tmp_path / "global.yaml"
        _write_price_config(config_path, "2024-01-03T00:00:00+00:00")
        _write_price_csv(
            price_dir,
            "BTCUSD_MAX_1DAY_FROM_PERPLEXITY.csv",
            [
                ("2024-01-01T00:00:00+00:00", 100.0, 110.0, 95.0, 105.0, 1000.0),
                ("2024-01-02T00:00:00+00:00", 105.0, 115.0, 101.0, 112.0, 1200.0),
                ("2024-01-03T00:00:00+00:00", 112.0, 116.0, 108.0, 114.0, 1400.0),
            ],
        )
        monkeypatch.setenv("PRICE_DATA_DIR", str(price_dir))
        monkeypatch.setenv("PRICE_HISTORY_CONFIG_PATH", str(config_path))
        monkeypatch.setenv("PRICE_HISTORY_DISABLE_LIVE_SYNC", "1")

        resp = client.get("/branches/price-history?assets=BTC")
        assert resp.status_code == 200

        payload = resp.json()
        assert list(payload["assets"]) == ["BTC"]
        assert [candle["date"] for candle in payload["assets"]["BTC"]] == [
            "2024-01-01",
            "2024-01-02",
        ]
        assert payload["assets"]["BTC"][-1]["close"] == pytest.approx(112.0)

    def test_price_history_fetches_and_caches_post_cutoff_live_data(
        self,
        client,
        tmp_db,
        tmp_path,
        monkeypatch,
    ):
        price_dir = tmp_path / "prices"
        price_dir.mkdir()
        config_path = tmp_path / "global.yaml"
        cutoff_date = "2024-01-03T00:00:00+00:00"
        cutoff_ms = int(datetime(2024, 1, 3, tzinfo=timezone.utc).timestamp() * 1000)
        _write_price_config(config_path, cutoff_date)
        _write_price_csv(
            price_dir,
            "BTCUSD_MAX_1DAY_FROM_PERPLEXITY.csv",
            [
                ("2024-01-01T00:00:00+00:00", 100.0, 110.0, 95.0, 105.0, 1000.0),
                ("2024-01-02T00:00:00+00:00", 105.0, 115.0, 101.0, 112.0, 1200.0),
                ("2024-01-03T00:00:00+00:00", 112.0, 118.0, 109.0, 115.0, 1300.0),
            ],
        )
        monkeypatch.setenv("PRICE_DATA_DIR", str(price_dir))
        monkeypatch.setenv("PRICE_HISTORY_CONFIG_PATH", str(config_path))
        monkeypatch.delenv("PRICE_HISTORY_DISABLE_LIVE_SYNC", raising=False)

        class MockHyperliquidClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc_val, exc_tb):
                return None

            async def get_candles(self, symbol: str, interval: str, start_time: int, end_time: int):
                assert symbol == "BTC"
                assert interval == "1d"
                assert start_time == cutoff_ms
                return [
                    {
                        "t": cutoff_ms,
                        "o": "112",
                        "h": "118",
                        "l": "109",
                        "c": "116",
                        "v": "1500",
                    }
                ]

        monkeypatch.setattr("data.price_history.HyperliquidClient", MockHyperliquidClient)

        resp = client.get("/branches/price-history?assets=BTC")
        assert resp.status_code == 200
        payload = resp.json()
        assert [candle["date"] for candle in payload["assets"]["BTC"]] == [
            "2024-01-01",
            "2024-01-02",
            "2024-01-03",
        ]
        assert payload["assets"]["BTC"][-1]["close"] == pytest.approx(116.0)

        conn = sqlite3.connect(str(tmp_db))
        rows = conn.execute(
            "SELECT source, close FROM ohlcv WHERE asset = 'BTC' AND timeframe = '1d' ORDER BY ts ASC, source ASC"
        ).fetchall()
        conn.close()
        assert rows == [
            ("csv", 105.0),
            ("csv", 112.0),
            ("hyperliquid", 116.0),
        ]

    def test_price_history_supports_post_cutoff_4h_live_hydration(self, client, tmp_path, monkeypatch):
        price_dir = tmp_path / "prices"
        price_dir.mkdir()
        config_path = tmp_path / "global.yaml"
        cutoff_date = "2024-02-01T00:00:00+00:00"
        _write_price_config(config_path, cutoff_date)
        monkeypatch.setenv("PRICE_DATA_DIR", str(price_dir))
        monkeypatch.setenv("PRICE_HISTORY_CONFIG_PATH", str(config_path))
        monkeypatch.delenv("PRICE_HISTORY_DISABLE_LIVE_SYNC", raising=False)

        class MockHyperliquidClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc_val, exc_tb):
                return None

            async def get_candles(self, symbol: str, interval: str, start_time: int, end_time: int):
                assert symbol == "SOL"
                assert interval == "4h"
                return [
                    {
                        "t": int(datetime(2024, 2, 1, 4, tzinfo=timezone.utc).timestamp() * 1000),
                        "o": "98",
                        "h": "102",
                        "l": "94",
                        "c": "100",
                        "v": "800",
                    }
                ]

        monkeypatch.setattr("data.price_history.HyperliquidClient", MockHyperliquidClient)

        resp = client.get("/branches/price-history?assets=SOL&timeframe=4h")
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["timeframe"] == "4h"
        assert [candle["date"] for candle in payload["assets"]["SOL"]] == [
            "2024-02-01T04:00:00+00:00"
        ]
        assert payload["assets"]["SOL"][-1]["close"] == pytest.approx(100.0)

    def test_price_history_refreshes_existing_live_candle_for_current_day(
        self,
        client,
        tmp_db,
        tmp_path,
        monkeypatch,
    ):
        price_dir = tmp_path / "prices"
        price_dir.mkdir()
        config_path = tmp_path / "global.yaml"
        monkeypatch.setenv("PRICE_DATA_DIR", str(price_dir))
        monkeypatch.setenv("PRICE_HISTORY_CONFIG_PATH", str(config_path))
        monkeypatch.delenv("PRICE_HISTORY_DISABLE_LIVE_SYNC", raising=False)

        day_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        day_start_ms = int(day_start.timestamp() * 1000)
        _write_price_config(config_path, day_start.isoformat())
        from db.schema import apply_schema
        conn = sqlite3.connect(str(tmp_db))
        apply_schema(conn)
        conn.execute(
            """
            INSERT INTO ohlcv (ts, asset, source, timeframe, open, high, low, close, volume)
            VALUES (?, 'BTC', 'hyperliquid', '1d', 100, 110, 90, 101, 500)
            """,
            (day_start_ms,),
        )
        conn.commit()
        conn.close()

        class MockHyperliquidClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc_val, exc_tb):
                return None

            async def get_candles(self, symbol: str, interval: str, start_time: int, end_time: int):
                assert symbol == "BTC"
                assert interval == "1d"
                assert start_time == day_start_ms
                return [
                    {
                        "t": day_start_ms,
                        "o": "100",
                        "h": "112",
                        "l": "90",
                        "c": "109",
                        "v": "900",
                    }
                ]

        monkeypatch.setattr("data.price_history.HyperliquidClient", MockHyperliquidClient)

        resp = client.get("/branches/price-history?assets=BTC")
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["assets"]["BTC"][-1]["date"] == day_start.date().isoformat()
        assert payload["assets"]["BTC"][-1]["close"] == pytest.approx(109.0)


class TestSavedBranchYaml:
    def test_list_branches_syncs_saved_yaml_files(self, client):
        branches_dir = Path(os.environ["BRANCHES_YAML_DIR"])
        _write_branch_yaml(
            branches_dir / "april.yaml",
            """
            version: 1
            branch:
              id: yaml-branch
              name: YAML Branch
              color: "#50d2c1"
              is_main: false
              fork_date: "2025-01-01"
              balance: 2500000
              positions:
                - id: pos-1
                  asset: ETH
                  direction: Long
                  mode: Cross
                  leverage: 5
                  margin: 40000
                  entry_date: "2025-04-16"
                  entry_price: 1577.27
                  exit_date: "2025-06-18"
                  exit_price: 2525.54
            """,
        )

        resp = client.get("/branches")
        assert resp.status_code == 200
        branch = next(item for item in resp.json() if item["id"] == "yaml-branch")
        assert branch["source_type"] == "yaml"
        assert branch["positions"][0]["exit_price"] == pytest.approx(2525.54)
        assert branch["positions"][0]["margin"] == pytest.approx(40000)
        assert branch["positions"][0]["leverage"] == pytest.approx(5)

    def test_import_endpoint_persists_yaml_file(self, client):
        branches_dir = Path(os.environ["BRANCHES_YAML_DIR"])
        resp = client.post(
            "/branches/import",
            json={
                "file_name": "custom-portfolio.yaml",
                "raw_text": """
                portfolio:
                  name: Imported Portfolio
                  balance: 2500000
                  positions:
                    - asset: BTC
                      direction: Long
                      mode: Cross
                      leverage: 15
                      margin: 33333.3333
                      entry_date: "2025-04-15"
                      entry_price: 83629.78
                      exit_date: "2025-07-30"
                      exit_price: 117830.15
                """,
            },
        )
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["name"] == "Imported Portfolio"
        assert payload["positions"][0]["margin"] == pytest.approx(33333.3333)
        assert payload["positions"][0]["leverage"] == pytest.approx(15)
        saved_file = branches_dir / "custom-portfolio.yaml"
        assert saved_file.exists()

    def test_updating_yaml_backed_branch_rewrites_file(self, client):
        branches_dir = Path(os.environ["BRANCHES_YAML_DIR"])
        yaml_path = branches_dir / "yaml-branch.yaml"
        _write_branch_yaml(
            yaml_path,
            """
            version: 1
            branch:
              id: yaml-branch
              name: YAML Branch
              color: "#50d2c1"
              is_main: false
              fork_date: "2025-01-01"
              balance: 2500000
              positions:
                - id: pos-1
                  asset: ETH
                  direction: Long
                  mode: Cross
                  leverage: 5
                  margin: 40000
                  entry_date: "2025-04-16"
                  entry_price: 1577.27
                  exit_date: "2025-06-18"
                  exit_price: 2525.54
            """,
        )

        assert client.get("/branches").status_code == 200
        resp = client.put("/branches/yaml-branch", json={"balance": 2600000})
        assert resp.status_code == 200

        import yaml

        with yaml_path.open() as handle:
            saved = yaml.safe_load(handle)
        assert saved["branch"]["balance"] == pytest.approx(2600000)
        assert "notional" not in saved["branch"]["positions"][0]
