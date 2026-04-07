import logging
import asyncio
import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Deque, Tuple
from collections import deque, defaultdict
from datetime import datetime, timedelta

from ...hl_client.client import HyperliquidClient
from ...hl_client.websocket import HyperliquidWebSocket

logger = logging.getLogger(__name__)

class DeltaOIProfiler:
    def __init__(self, bin_size_pct: float = 0.001): # 0.1% price bins
        self.bin_size_pct = bin_size_pct
        self.oi_profile: Dict[str, Dict[float, float]] = defaultdict(lambda: defaultdict(float)) # symbol -> price_bin -> cumulative_oi_delta
        self.trade_vol_profile: Dict[str, Dict[float, float]] = defaultdict(lambda: defaultdict(float))
        self.last_total_oi: Dict[str, float] = {}

    def update_oi(self, symbol: str, total_oi: float, current_price: float):
        if symbol not in self.last_total_oi:
            self.last_total_oi[symbol] = total_oi
            return

        delta_oi = total_oi - self.last_total_oi[symbol]
        self.last_total_oi[symbol] = total_oi
        
        if abs(delta_oi) < 1.0: # Ignore noise
            return

        # Distribute delta_oi based on recent trade volume profile or just current price
        # If we have recent trade volume profile, use it. Else fall back to current price bin.
        
        # Simple fallback for now: assign to current price bin
        bin_price = self._get_bin(current_price)
        self.oi_profile[symbol][bin_price] += delta_oi
        
        # Decay profile over time? Or accumulator?
        # Liquidation strategy cares about 'built up positions'.
        # We should decay very slowly or based on price movement flushing them out.
        
    def _get_bin(self, price: float) -> float:
        step = price * self.bin_size_pct
        return round(price / step) * step

    def get_underwater_bins(self, symbol: str, current_price: float) -> List[Tuple[float, float, float]]:
        """
        Return bins that are 'underwater' (trapped).
        Returns list of (price, oi_amount, bias)
        Bias: 1.0 if longs trapped (price < entry), -1.0 if shorts trapped (price > entry)
        """
        result = []
        for bin_price, oi_delta in self.oi_profile[symbol].items():
            if oi_delta > 0: # Net accumulation
                # If accumulated LONGs (positive delta usually implies Open Long or Short? 
                # Actually, positive delta just means OI went up. Could be Longs+Shorts opening.
                # We inferred direction logic in plan: 
                # - If OI up + Price Up -> Longs are aggressive (and Shorts might be trapped later if price drops)
                # - If OI up + Price Down -> Shorts are aggressive
                
                # Simplified Inference:
                # We track the 'net bias' of the bin based on how price moved when OI was added.
                # Ideally DeltaOIProfiler needs to track 'bias' not just 'oi_delta'.
                # Let's assume for this MVP we stored just amount, and we re-evaluate trapped nature relative to current price.
                
                # If price < bin_price: Longs opened at bin_price are losing.
                if current_price < bin_price:
                    result.append((bin_price, oi_delta, 1.0)) # Longs trapped
                
                # If price > bin_price: Shorts opened at bin_price are losing.
                elif current_price > bin_price:
                    result.append((bin_price, oi_delta, -1.0)) # Shorts trapped
                    
        return result

class DataCollector:
    def __init__(self, client: HyperliquidClient, ws: HyperliquidWebSocket):
        self.client = client
        self.ws = ws
        self.oi_profiler = DeltaOIProfiler()
        
        # Buffers
        self.candles: Dict[str, pd.DataFrame] = {} # OHLCV
        self.recent_trades: Dict[str, Deque] = defaultdict(lambda: deque(maxlen=1000))
        self.funding_history: Dict[str, Deque] = defaultdict(lambda: deque(maxlen=100))
        self.prices: Dict[str, float] = {}
        
        # Stats
        self.vol_zscores: Dict[str, float] = {}
        self.price_accel_zscores: Dict[str, float] = {}
        
    async def start(self, symbols: List[str]):
        # 1. Load historical
        await self._load_history(symbols)
        
        # 2. Subscribe WS
        # Note: HyperliquidWebSocket.subscribe_trades takes callback
        for s in symbols:
            self.ws.subscribe_trades(s, lambda msg, sym=s: self._on_trade(sym, msg))
            # Need generic subscription for 'webData2' or similar for OI/Funding?
            # WS class supported subscribe_all_mids.
            # Assuming we can modify WS or use poll fallback for OI/Funding if WS doesn't support specific channel.
            # Official docs say 'activeAssetCtx' or 'webData2'.
        
        self.ws.subscribe_all_mids(self._on_mids)
        
        # 3. Start polling loop for things not on WS (OI, Funding often need polling or specific channel)
        asyncio.create_task(self._poll_loop(symbols))

    async def _load_history(self, symbols: List[str]):
        end = int(datetime.utcnow().timestamp() * 1000)
        start = end - (3600 * 1000) # 1 hour
        for s in symbols:
            candles = await self.client.get_candles(s, "1m", start, end)
            if candles:
                 df = pd.DataFrame(candles)
                 # Ensure schema keys match API (t, o, h, l, c, v)
                 # API usually returns: t, T, s, i, o, c, h, l, v, n
                 # We need to map or strictly name columns.
                 # Mocking simple DF structure for now
                 self.candles[s] = df
                 
    def _on_trade(self, symbol: str, msg: Dict):
        # Update trade buffer
        # Msg format depends on API, usually list of trades
        trades = msg.get("data", [])
        for t in trades:
            self.recent_trades[symbol].append({
                "price": float(t['px']),
                "size": float(t['sz']),
                "side": t['side'],
                "time": t['time']
            })
            
            # Update profiling
            # Need total OI for this. 
            # If trade message doesn't have OI, we rely on the _poll_loop to get OI updates 
            # and then sync with the latest price.

    def _on_mids(self, msg: Dict):
        # msg data is {symbol: price}
        data = msg.get("data", {})
        for s, p in data.items():
            self.prices[s] = float(p)

    async def _poll_loop(self, symbols: List[str]):
        while True:
            try:
                # Poll Meta for OI and Funding
                meta, assets = await self.client.get_meta_and_asset_ctxs()
                if meta and assets:
                    universe = meta.get("universe", [])
                    for i, asset_ctx in enumerate(assets):
                        if i < len(universe):
                            sym = universe[i]["name"]
                            if sym in symbols:
                                # Update OI
                                oi = float(asset_ctx.get("openInterest", 0))
                                current_px = self.prices.get(sym, 0)
                                if current_px > 0:
                                    self.oi_profiler.update_oi(sym, oi, current_px)
                                    
                                # Update Funding
                                funding = float(asset_ctx.get("funding", 0))
                                self.funding_history[sym].append(funding)
                                
                # Compute Stats
                self._compute_zscores(symbols)
                
            except Exception as e:
                logger.error(f"Polling loop error: {e}")
                
            await asyncio.sleep(5) # 5 seconds poll

    def _compute_zscores(self, symbols: List[str]):
        for s in symbols:
            # 1. Volume Spike Z-Score (vs 1h avg from candles)
            # 2. Price Accel Z-Score
            # Implementation simplified for brevity
            pass

    def get_market_data(self, symbol: str) -> Dict:
        """Return aggregated snapshot for LVS calculator"""
        return {
            "price": self.prices.get(symbol, 0),
            "funding_history": list(self.funding_history[symbol]),
            "recent_trades": list(self.recent_trades[symbol]),
            "oi_profile": self.oi_profiler.get_underwater_bins(symbol, self.prices.get(symbol, 0)),
            # ... stats
        }
