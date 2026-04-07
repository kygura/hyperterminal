from __future__ import annotations

import csv
import logging
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

import yaml

from data.hl_client.client import HyperliquidClient
from db.schema import apply_schema

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).resolve().parent.parent / "data.db"
DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "global.yaml"
CSV_PRICE_FILES = {
    "BTC": "BTCUSD_MAX_1DAY_FROM_PERPLEXITY.csv",
    "ETH": "ETHUSD_MAX_1DAY_FROM_PERPLEXITY.csv",
    "HYPE": "HYPEUSD_MAX_1DAY_FROM_PERPLEXITY.csv",
}
DEFAULT_ASSETS = ("BTC", "ETH", "SOL", "HYPE")
DEFAULT_HYDRATION_TIMEFRAMES = ("1h", "4h", "1d")
CSV_SOURCE = "csv"
LIVE_SOURCE = "hyperliquid"
TIMEFRAME_BUCKETS_MS = {
    "1h": 60 * 60 * 1000,
    "4h": 4 * 60 * 60 * 1000,
    "1d": 24 * 60 * 60 * 1000,
}


def _db_file() -> Path:
    override = os.getenv("BRANCHES_DB_PATH", "").strip()
    if override:
        return Path(override)
    return DB_PATH


def _data_dir() -> Path:
    override = os.getenv("PRICE_DATA_DIR", "").strip()
    if override:
        return Path(override)
    return DATA_DIR


def _config_path() -> Path:
    override = os.getenv("PRICE_HISTORY_CONFIG_PATH", "").strip()
    if override:
        return Path(override)
    return CONFIG_PATH


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_db_file()), check_same_thread=False, timeout=5)
    conn.row_factory = sqlite3.Row
    apply_schema(conn)
    return conn


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_timestamp(value: str) -> int:
    text = value.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.astimezone(timezone.utc).timestamp() * 1000)


def _format_label(timestamp_ms: int, timeframe: str) -> str:
    moment = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)
    if timeframe == "1d":
        return moment.date().isoformat()
    return moment.isoformat()


def _normalize_assets(assets: Iterable[str] | None) -> list[str]:
    resolved = [asset.strip().upper() for asset in (assets or DEFAULT_ASSETS) if asset and asset.strip()]
    deduped: list[str] = []
    for asset in resolved:
        if asset not in deduped:
            deduped.append(asset)
    return deduped


def _normalize_timeframe(timeframe: str | None) -> str:
    raw = (timeframe or "1d").strip().lower()
    aliases = {
        "1h": "1h",
        "hour": "1h",
        "hourly": "1h",
        "4h": "4h",
        "4hour": "4h",
        "4hourly": "4h",
        "1d": "1d",
        "d": "1d",
        "day": "1d",
        "daily": "1d",
    }
    normalized = aliases.get(raw, raw)
    if normalized not in TIMEFRAME_BUCKETS_MS:
        raise ValueError(f"Unsupported timeframe: {timeframe}")
    return normalized


def _current_bucket_start_ms(timeframe: str, at: datetime | None = None) -> int:
    current = at or _utcnow()
    if timeframe == "1d":
        start = datetime.combine(current.date(), datetime.min.time(), tzinfo=timezone.utc)
        return int(start.timestamp() * 1000)

    bucket_ms = TIMEFRAME_BUCKETS_MS[timeframe]
    now_ms = int(current.timestamp() * 1000)
    return now_ms - (now_ms % bucket_ms)


def _load_price_history_config() -> dict:
    path = _config_path()
    if not path.is_file():
        return {}

    with path.open("r", encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle) or {}

    return loaded.get("price_history", {}) if isinstance(loaded, dict) else {}


def _configured_cutoff_ms(config: dict) -> int | None:
    cutoff_date = str(config.get("cutoff_date") or "").strip()
    if not cutoff_date:
        return None
    return _parse_timestamp(cutoff_date)


def _configured_assets(config: dict) -> list[str]:
    configured = config.get("assets")
    if isinstance(configured, list):
        return _normalize_assets(configured)
    return list(DEFAULT_ASSETS)


def _configured_hydration_timeframes(config: dict) -> list[str]:
    configured = config.get("hydration_timeframes")
    if not isinstance(configured, list) or not configured:
        configured = list(DEFAULT_HYDRATION_TIMEFRAMES)

    normalized: list[str] = []
    for timeframe in configured:
        try:
            candidate = _normalize_timeframe(str(timeframe))
        except ValueError:
            continue
        if candidate not in normalized:
            normalized.append(candidate)
    return normalized or list(DEFAULT_HYDRATION_TIMEFRAMES)


def _upsert_candle(
    conn: sqlite3.Connection,
    *,
    ts: int,
    asset: str,
    source: str,
    timeframe: str,
    open_price: float,
    high_price: float,
    low_price: float,
    close_price: float,
    volume: float,
) -> None:
    conn.execute(
        """
        INSERT INTO ohlcv (ts, asset, source, timeframe, open, high, low, close, volume)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(asset, source, timeframe, ts) DO UPDATE SET
            open = excluded.open,
            high = excluded.high,
            low = excluded.low,
            close = excluded.close,
            volume = excluded.volume
        """,
        (
            ts,
            asset,
            source,
            timeframe,
            open_price,
            high_price,
            low_price,
            close_price,
            volume,
        ),
    )


def seed_csv_price_history(
    conn: sqlite3.Connection,
    assets: Iterable[str],
    *,
    cutoff_ms: int | None,
) -> dict[str, int]:
    cutoffs: dict[str, int] = {}
    data_dir = _data_dir()

    for asset in _normalize_assets(assets):
        filename = CSV_PRICE_FILES.get(asset)
        if not filename:
            continue

        csv_path = data_dir / filename
        if not csv_path.is_file():
            logger.warning("Historical CSV missing for %s at %s", asset, csv_path)
            continue

        max_ts = 0
        with csv_path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                ts = _parse_timestamp(row["date"])
                if cutoff_ms is not None and ts >= cutoff_ms:
                    continue
                max_ts = max(max_ts, ts)
                _upsert_candle(
                    conn,
                    ts=ts,
                    asset=asset,
                    source=CSV_SOURCE,
                    timeframe="1d",
                    open_price=float(row["open"]),
                    high_price=float(row["high"]),
                    low_price=float(row["low"]),
                    close_price=float(row["close"]),
                    volume=float(row["volume"]),
                )

        if max_ts:
            cutoffs[asset] = max_ts

    conn.commit()
    return cutoffs


async def sync_live_price_history(
    conn: sqlite3.Connection,
    assets: Iterable[str],
    *,
    timeframe: str,
    cutoff_ms: int | None,
    csv_cutoffs: dict[str, int] | None = None,
) -> None:
    if os.getenv("PRICE_HISTORY_DISABLE_LIVE_SYNC", "").strip().lower() in {"1", "true", "yes"}:
        return

    target_assets = _normalize_assets(assets)
    if not target_assets:
        return

    bucket_ms = TIMEFRAME_BUCKETS_MS[timeframe]
    current_bucket_start = _current_bucket_start_ms(timeframe)
    end_ms = int((_utcnow() + timedelta(milliseconds=bucket_ms)).timestamp() * 1000)
    csv_cutoffs = csv_cutoffs or {}

    async with HyperliquidClient() as client:
        for asset in target_assets:
            row = conn.execute(
                """
                SELECT MAX(ts) AS latest_ts
                FROM ohlcv
                WHERE asset = ? AND source = ? AND timeframe = ?
                """,
                (asset, LIVE_SOURCE, timeframe),
            ).fetchone()
            latest_live_ts = int(row["latest_ts"]) if row and row["latest_ts"] is not None else 0

            base_start = cutoff_ms if cutoff_ms is not None else 0
            if timeframe == "1d" and base_start == 0:
                base_start = csv_cutoffs.get(asset, 0) + 1 if csv_cutoffs.get(asset) else 0
            elif timeframe == "1d" and csv_cutoffs.get(asset):
                base_start = max(base_start, csv_cutoffs[asset] + 1)

            if latest_live_ts >= current_bucket_start:
                start_ms = latest_live_ts
            elif latest_live_ts:
                start_ms = max(base_start, latest_live_ts + 1)
            else:
                start_ms = base_start

            if start_ms <= 0:
                start_ms = max(current_bucket_start - (365 * 24 * 60 * 60 * 1000), 0)

            try:
                candles = await client.get_candles(asset, timeframe, start_ms, end_ms)
            except Exception as exc:
                logger.warning(
                    "Failed syncing live %s price history for %s: %s",
                    timeframe,
                    asset,
                    exc,
                )
                continue

            inserted = 0
            for candle in candles:
                try:
                    ts = int(candle["t"])
                    if cutoff_ms is not None and ts < cutoff_ms:
                        continue
                    _upsert_candle(
                        conn,
                        ts=ts,
                        asset=asset,
                        source=LIVE_SOURCE,
                        timeframe=timeframe,
                        open_price=float(candle["o"]),
                        high_price=float(candle["h"]),
                        low_price=float(candle["l"]),
                        close_price=float(candle["c"]),
                        volume=float(candle["v"]),
                    )
                    inserted += 1
                except (KeyError, TypeError, ValueError) as exc:
                    logger.warning("Skipping malformed %s candle for %s: %s", timeframe, asset, exc)

            if inserted:
                conn.commit()


def load_price_dataset(
    conn: sqlite3.Connection,
    assets: Iterable[str],
    *,
    timeframe: str,
) -> dict:
    target_assets = _normalize_assets(assets)
    if not target_assets:
        return {
            "generatedAt": _utcnow().isoformat(),
            "timeframe": timeframe,
            "days": 0,
            "assets": {},
        }

    placeholders = ",".join("?" for _ in target_assets)
    params = [timeframe, LIVE_SOURCE, *target_assets]
    source_filter = "source = ?"

    if timeframe == "1d":
        source_filter = "source IN (?, ?)"
        params = [timeframe, CSV_SOURCE, LIVE_SOURCE, *target_assets]

    rows = conn.execute(
        f"""
        SELECT ts, asset, source, open, high, low, close, volume
        FROM ohlcv
        WHERE timeframe = ?
          AND {source_filter}
          AND asset IN ({placeholders})
        ORDER BY asset ASC, ts ASC
        """,
        params,
    ).fetchall()

    assets_map: dict[str, dict[str, tuple[int, dict]]] = {asset: {} for asset in target_assets}

    for row in rows:
        asset = str(row["asset"]).upper()
        candle = {
            "date": _format_label(int(row["ts"]), timeframe),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": float(row["volume"]),
        }
        existing = assets_map.setdefault(asset, {}).get(candle["date"])
        priority = 0 if row["source"] == LIVE_SOURCE else 1
        if existing is None or priority < existing[0]:
            assets_map[asset][candle["date"]] = (priority, candle)

    normalized_assets = {
        asset: [entry for _, entry in sorted(date_map.values(), key=lambda item: item[1]["date"])]
        for asset, date_map in assets_map.items()
        if date_map
    }

    point_count = max((len(series) for series in normalized_assets.values()), default=0)
    return {
        "generatedAt": _utcnow().isoformat(),
        "timeframe": timeframe,
        "days": point_count,
        "assets": normalized_assets,
    }


async def get_price_dataset(
    assets: Iterable[str] | None = None,
    timeframe: str | None = None,
) -> dict:
    config = _load_price_history_config()
    requested_assets = _normalize_assets(assets or _configured_assets(config))
    requested_timeframe = _normalize_timeframe(timeframe)
    cutoff_ms = _configured_cutoff_ms(config)
    hydration_timeframes = _configured_hydration_timeframes(config)

    conn = _connect()
    try:
        csv_cutoffs = seed_csv_price_history(conn, requested_assets, cutoff_ms=cutoff_ms)
        if requested_timeframe in hydration_timeframes:
            await sync_live_price_history(
                conn,
                requested_assets,
                timeframe=requested_timeframe,
                cutoff_ms=cutoff_ms,
                csv_cutoffs=csv_cutoffs,
            )

        dataset = load_price_dataset(conn, requested_assets, timeframe=requested_timeframe)
        dataset["cutoffDate"] = (
            datetime.fromtimestamp(cutoff_ms / 1000, tz=timezone.utc).isoformat()
            if cutoff_ms is not None
            else None
        )
        return dataset
    finally:
        conn.close()
