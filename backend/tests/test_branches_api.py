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


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def patch_branches_db(tmp_db, monkeypatch):
    monkeypatch.setenv("BRANCHES_DB_PATH", str(tmp_db))


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
