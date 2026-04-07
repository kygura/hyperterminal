"""
Tests for quant/api/news.py
Covers: GET /api/news, /sources, POST /sources, DELETE /sources/{id}
"""
import sqlite3
import json
import uuid
import pytest
from datetime import datetime
from fastapi import FastAPI
from fastapi.testclient import TestClient


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def patch_news_db(tmp_db, monkeypatch):
    """Point the news module at our isolated test DB (tables already created in conftest)."""
    monkeypatch.setenv("NEWS_DB_PATH", str(tmp_db))
    # _news_conn reads the env var at call time via _CANDIDATES, patch the function directly
    import api.news as news_mod
    _orig = news_mod._news_conn
    def _patched_conn():
        conn = sqlite3.connect(str(tmp_db), check_same_thread=False, timeout=5)
        conn.row_factory = sqlite3.Row
        return conn
    monkeypatch.setattr(news_mod, "_news_conn", _patched_conn)


@pytest.fixture()
def client():
    from api.news import router
    app = FastAPI()
    app.include_router(router)   # router already has prefix="/news"
    return TestClient(app)


def _insert_article(db_path, **kwargs):
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    defaults = dict(
        id=str(uuid.uuid4()),
        source="CoinDesk",
        title="Test Article",
        url=f"https://example.com/{uuid.uuid4()}",
        published_at=now,
        summary="A test article.",
        sentiment="bullish",
        confidence=0.85,
        impact="medium",
        affected_assets=json.dumps(["BTC"]),
        reasoning="Test",
        processed_at=now,
    )
    defaults.update(kwargs)
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """INSERT INTO news_articles
           (id, source, title, url, published_at, summary, sentiment,
            confidence, impact, affected_assets, reasoning, processed_at)
           VALUES (:id,:source,:title,:url,:published_at,:summary,:sentiment,
                   :confidence,:impact,:affected_assets,:reasoning,:processed_at)""",
        defaults,
    )
    conn.commit()
    conn.close()
    return defaults


def _get_items(resp) -> list:
    """Extract items from either a paginated {items: [...]} or plain list response."""
    data = resp.json()
    return data["items"] if isinstance(data, dict) and "items" in data else data


# ─── GET /news ────────────────────────────────────────────────────────────────

class TestGetNews:
    def test_returns_empty_items(self, client):
        resp = client.get("/news/")
        assert resp.status_code == 200
        assert _get_items(resp) == []

    def test_returns_inserted_articles(self, client, tmp_db):
        _insert_article(tmp_db, title="Article 1")
        _insert_article(tmp_db, title="Article 2")

        resp = client.get("/news/")
        assert resp.status_code == 200
        assert len(_get_items(resp)) == 2

    def test_filter_by_sentiment(self, client, tmp_db):
        _insert_article(tmp_db, sentiment="bullish")
        _insert_article(tmp_db, sentiment="bearish")

        resp = client.get("/news/?sentiment=bullish")
        items = _get_items(resp)
        assert len(items) == 1
        assert items[0]["sentiment"] == "bullish"

    def test_filter_by_asset(self, client, tmp_db):
        _insert_article(tmp_db, affected_assets=json.dumps(["BTC", "ETH"]))
        _insert_article(tmp_db, affected_assets=json.dumps(["SOL"]))

        resp = client.get("/news/?asset=BTC")
        assert resp.status_code == 200
        assert len(_get_items(resp)) == 1

    def test_limit_and_offset(self, client, tmp_db):
        for i in range(5):
            _insert_article(tmp_db, title=f"Article {i}")

        resp1 = client.get("/news/?limit=3&offset=0")
        assert len(_get_items(resp1)) == 3

        resp2 = client.get("/news/?limit=5&offset=3")
        assert len(_get_items(resp2)) == 2

    def test_affected_assets_is_list(self, client, tmp_db):
        _insert_article(tmp_db, affected_assets=json.dumps(["BTC", "ETH"]))

        resp = client.get("/news/")
        items = _get_items(resp)
        assert isinstance(items[0]["affected_assets"], list)
        assert "BTC" in items[0]["affected_assets"]


# ─── GET /news/sources ────────────────────────────────────────────────────────

class TestNewsSources:
    def test_get_sources_returns_list(self, client):
        resp = client.get("/news/sources")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_add_source(self, client):
        resp = client.post(
            "/news/sources",
            json={"name": "CoinDesk", "url": "https://coindesk.com/feed"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "CoinDesk"
        assert "id" in data

    def test_added_source_appears_in_list(self, client):
        client.post("/news/sources", json={"name": "CryptoNews", "url": "https://crypto.news/rss"})
        sources = client.get("/news/sources").json()
        assert any(s["url"] == "https://crypto.news/rss" for s in sources)

    def test_add_duplicate_source_url_fails(self, client):
        client.post("/news/sources", json={"name": "A", "url": "https://unique.com/feed"})
        resp2 = client.post("/news/sources", json={"name": "B", "url": "https://unique.com/feed"})
        assert resp2.status_code in (400, 409, 422, 500)

    def test_delete_source(self, client):
        add_resp = client.post(
            "/news/sources",
            json={"name": "TestSource", "url": "https://test-delete.com/rss"},
        )
        assert add_resp.status_code == 200
        source_id = add_resp.json()["id"]

        del_resp = client.delete(f"/news/sources/{source_id}")
        assert del_resp.status_code == 200

        sources = client.get("/news/sources").json()
        assert not any(s["id"] == source_id for s in sources)

    def test_delete_nonexistent_source_is_idempotent(self, client):
        # The delete endpoint does DELETE WHERE id=? without checking rowcount,
        # so deleting a non-existent source is a no-op returning 200.
        resp = client.delete("/news/sources/99999")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
