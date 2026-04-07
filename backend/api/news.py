"""
RSS News Feed + LLM Sentiment Classification
Polls configured RSS feeds, classifies articles with Claude Haiku, stores in SQLite.
"""
import json
import sqlite3
import os
import asyncio
import hashlib
import logging
from pathlib import Path
from typing import Optional, List
from datetime import datetime
from fastapi import APIRouter, Query, HTTPException, BackgroundTasks
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/news", tags=["news"])

# --- Database setup ---

_DB_PATH = Path(os.getenv("NEWS_DB_PATH", "")) or Path(__file__).resolve().parent.parent / "data.db"

def _news_conn() -> sqlite3.Connection:
    candidates = [
        Path(os.getenv("NEWS_DB_PATH", "")),
        Path(__file__).resolve().parent.parent / "data.db",
    ]
    db = next((p for p in candidates if str(p) and p.is_file()), None)
    if not db:
        # Create in the repo root
        db = Path(__file__).resolve().parent.parent / "data.db"
    conn = sqlite3.connect(str(db), check_same_thread=False, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_news_tables():
    conn = _news_conn()
    conn.executescript("""
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
            enabled INTEGER DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS signal_article_links (
            signal_id TEXT,
            article_id TEXT,
            PRIMARY KEY (signal_id, article_id)
        );
    """)

    # Seed default sources if empty
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM news_sources")
    if cur.fetchone()[0] == 0:
        default_sources = [
            ("The Block", "https://www.theblock.co/rss.xml"),
            ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
            ("Cointelegraph", "https://cointelegraph.com/rss"),
            ("DL News", "https://www.dlnews.com/rss/"),
        ]
        conn.executemany("INSERT OR IGNORE INTO news_sources (name, url) VALUES (?, ?)", default_sources)
        conn.commit()
    conn.close()


try:
    _ensure_news_tables()
except Exception as e:
    logger.warning(f"Could not init news tables: {e}")


# --- Models ---

class NewsSource(BaseModel):
    id: Optional[int] = None
    name: str
    url: str
    enabled: bool = True


class NewsArticle(BaseModel):
    id: str
    source: str
    title: str
    url: str
    published_at: str
    summary: str
    sentiment: str
    confidence: float
    impact: str
    affected_assets: List[str]
    reasoning: str
    processed_at: Optional[str] = None


# --- Endpoints ---

@router.get("")
def list_news(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    asset: Optional[str] = Query(None),
    sentiment: Optional[str] = Query(None),
    impact: Optional[str] = Query(None),
):
    try:
        conn = _news_conn()
        conditions = []
        params: list = []

        if sentiment:
            conditions.append("sentiment = ?")
            params.append(sentiment)
        if impact:
            conditions.append("impact = ?")
            params.append(impact)
        if asset:
            conditions.append("affected_assets LIKE ?")
            params.append(f'%"{asset}"%')

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        cur = conn.execute(
            f"SELECT * FROM news_articles {where} ORDER BY published_at DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        )
        rows = cur.fetchall()
        conn.close()

        items = []
        for r in rows:
            d = dict(r)
            if isinstance(d.get("affected_assets"), str):
                try:
                    d["affected_assets"] = json.loads(d["affected_assets"])
                except Exception:
                    d["affected_assets"] = []
            items.append(d)

        return {"items": items, "total": len(items), "offset": offset}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sources")
def list_sources():
    try:
        conn = _news_conn()
        rows = conn.execute("SELECT * FROM news_sources ORDER BY id").fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/sources")
def add_source(source: NewsSource):
    try:
        conn = _news_conn()
        conn.execute(
            "INSERT INTO news_sources (name, url, enabled) VALUES (?, ?, ?)",
            (source.name, source.url, 1 if source.enabled else 0),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM news_sources WHERE url = ?", (source.url,)).fetchone()
        conn.close()
        return dict(row)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/sources/{source_id}")
def delete_source(source_id: int):
    try:
        conn = _news_conn()
        conn.execute("DELETE FROM news_sources WHERE id = ?", (source_id,))
        conn.commit()
        conn.close()
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/refresh")
async def trigger_refresh(background_tasks: BackgroundTasks):
    """Manually trigger a news poll cycle."""
    background_tasks.add_task(_poll_news_cycle)
    return {"status": "refresh triggered"}


# --- Background polling ---

async def _classify_article(title: str, summary: str) -> dict:
    """Classify article sentiment using the configured LLM provider."""
    from lib.llm import complete

    system = "You are a crypto market sentiment classifier. Respond ONLY with valid JSON, no markdown."
    user = (
        f"Classify this crypto news article's market impact:\n"
        f"Title: {title}\n"
        f"Summary: {summary[:500]}\n\n"
        f'Respond with exactly: {{"sentiment": "bullish"|"bearish"|"neutral", '
        f'"confidence": 0.0-1.0, "impact": "high"|"medium"|"low", '
        f'"affected_assets": ["BTC", "ETH", ...], '
        f'"reasoning": "one sentence"}}'
    )

    try:
        text = await complete(system=system, user=user, max_tokens=256)
        return json.loads(text)
    except EnvironmentError as e:
        logger.warning(f"LLM not configured: {e}")
        return {
            "sentiment": "neutral", "confidence": 0.5, "impact": "low",
            "affected_assets": [], "reasoning": "No LLM API key configured",
        }
    except Exception as e:
        logger.error(f"LLM classification failed: {e}")
        return {
            "sentiment": "neutral", "confidence": 0.5, "impact": "low",
            "affected_assets": [], "reasoning": "Classification unavailable",
        }


async def _poll_news_cycle():
    """Fetch all RSS sources, classify new articles, store in DB."""
    try:
        import feedparser  # type: ignore
        import httpx

        conn = _news_conn()
        sources = conn.execute("SELECT * FROM news_sources WHERE enabled = 1").fetchall()

        for source_row in sources:
            source_name = source_row["name"]
            source_url = source_row["url"]

            try:
                async with httpx.AsyncClient(timeout=20) as client:
                    r = await client.get(source_url, headers={"User-Agent": "HyperTrade/1.0"})
                    r.raise_for_status()
                    feed = feedparser.parse(r.text)
            except Exception as e:
                logger.warning(f"Failed to fetch {source_url}: {e}")
                continue

            for entry in feed.entries[:20]:
                url = entry.get("link", "")
                if not url:
                    continue

                # Check if already exists
                existing = conn.execute(
                    "SELECT id FROM news_articles WHERE url = ?", (url,)
                ).fetchone()
                if existing:
                    continue

                title = entry.get("title", "")
                summary = entry.get("summary", entry.get("description", ""))
                # Strip HTML tags roughly
                import re
                summary = re.sub(r"<[^>]+>", "", summary).strip()

                published_raw = entry.get("published", entry.get("updated", ""))
                try:
                    from email.utils import parsedate_to_datetime
                    published_at = parsedate_to_datetime(published_raw).isoformat()
                except Exception:
                    published_at = datetime.utcnow().isoformat()

                article_id = hashlib.sha256(url.encode()).hexdigest()[:16]

                # Classify
                classification = await _classify_article(title, summary)

                conn.execute(
                    """INSERT OR IGNORE INTO news_articles
                       (id, source, title, url, published_at, summary, sentiment, confidence, impact, affected_assets, reasoning, processed_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        article_id,
                        source_name,
                        title,
                        url,
                        published_at,
                        summary[:1000],
                        classification.get("sentiment", "neutral"),
                        float(classification.get("confidence", 0.5)),
                        classification.get("impact", "low"),
                        json.dumps(classification.get("affected_assets", [])),
                        classification.get("reasoning", ""),
                        datetime.utcnow().isoformat(),
                    ),
                )
                conn.commit()
                logger.info(f"Stored article: {title[:60]}")

                await asyncio.sleep(0.5)  # Rate limit LLM calls

        conn.close()
    except Exception as e:
        logger.error(f"News poll cycle failed: {e}")


async def start_news_polling(interval_minutes: int = 10):
    """Long-running background task that polls news periodically."""
    logger.info(f"Starting news polling every {interval_minutes} minutes")
    while True:
        await _poll_news_cycle()
        await asyncio.sleep(interval_minutes * 60)
