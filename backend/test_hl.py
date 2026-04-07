import asyncio
from data.hl_client.client import HyperliquidClient

async def main():
    async with HyperliquidClient() as client:
        # Get BTC candles for last 24h
        import time
        now = int(time.time() * 1000)
        day_ago = now - 24 * 60 * 60 * 1000
        candles = await client.get_candles("BTC", "1h", day_ago, now)
        print(candles[:2])

asyncio.run(main())
