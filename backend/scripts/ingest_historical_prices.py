import os
import sys
import sqlite3
import csv
import time
import asyncio
from datetime import datetime, timezone
from pathlib import Path

# Add backend directory to sys.path to allow imports
sys.path.append(str(Path(__file__).resolve().parent.parent))

from data.hl_client.client import HyperliquidClient
from db.schema import apply_schema

DB_PATH = Path(__file__).resolve().parent.parent / "data.db"
DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    apply_schema(conn)
    conn.close()

def parse_date_to_ms(date_str: str) -> int:
    dt = datetime.fromisoformat(date_str)
    return int(dt.timestamp() * 1000)

def ingest_csvs():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    files = {
        "BTCUSD_MAX_1DAY_FROM_PERPLEXITY.csv": "BTC",
        "ETHUSD_MAX_1DAY_FROM_PERPLEXITY.csv": "ETH",
        "HYPEUSD_MAX_1DAY_FROM_PERPLEXITY.csv": "HYPE"
    }
    
    last_timestamps = {}
    
    for filename, asset in files.items():
        filepath = DATA_DIR / filename
        if not filepath.exists():
            print(f"File not found: {filepath}")
            continue
            
        print(f"Ingesting {filename} as {asset}...")
        with open(filepath, 'r') as f:
            reader = csv.DictReader(f)
            count = 0
            max_ts = 0
            for row in reader:
                ts = parse_date_to_ms(row['date'])
                max_ts = max(max_ts, ts)
                cursor.execute("""
                    INSERT OR IGNORE INTO ohlcv 
                    (ts, asset, source, timeframe, open, high, low, close, volume)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    ts,
                    asset,
                    'csv',
                    '1d',
                    float(row['open']),
                    float(row['high']),
                    float(row['low']),
                    float(row['close']),
                    float(row['volume'])
                ))
                count += 1
            print(f"Inserted {count} rows for {asset}")
            last_timestamps[asset] = max_ts
            
    conn.commit()
    conn.close()
    return last_timestamps

async def fetch_recent_from_hl(last_timestamps: dict):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    now_ms = int(time.time() * 1000)
    
    async with HyperliquidClient() as client:
        for asset, last_ts in last_timestamps.items():
            print(f"Fetching recent data for {asset} from {last_ts} to {now_ms}...")
            
            start_ts = last_ts
            while start_ts < now_ms:
                end_ts = min(start_ts + (2000 * 60 * 60 * 1000), now_ms)
                candles = await client.get_candles(asset, "1h", start_ts, end_ts)
                
                if not candles:
                    break
                    
                for c in candles:
                    ts = int(c['t'])
                    cursor.execute("""
                        INSERT OR IGNORE INTO ohlcv 
                        (ts, asset, source, timeframe, open, high, low, close, volume)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        ts,
                        asset,
                        'hyperliquid',
                        '1h',
                        float(c['o']),
                        float(c['h']),
                        float(c['l']),
                        float(c['c']),
                        float(c['v'])
                    ))
                conn.commit()
                print(f"Inserted {len(candles)} recent candles for {asset}")
                
                if len(candles) > 0:
                    next_start_ts = int(candles[-1]['T']) + 1
                    if next_start_ts <= start_ts:
                        break
                    start_ts = next_start_ts
                else:
                    break
                await asyncio.sleep(0.5)
                
    conn.close()

if __name__ == "__main__":
    print("Starting historical data ingestion...")
    init_db()
    last_ts = ingest_csvs()
    print("CSVs ingested. Last timestamps:", last_ts)
    asyncio.run(fetch_recent_from_hl(last_ts))
    print("Done.")
