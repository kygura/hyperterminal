import asyncio
import logging
import time
import re
from typing import Set, Dict, List, Optional, Any
from collections import deque
from datetime import datetime, timedelta

from ...hl_client.client import HyperliquidClient

logger = logging.getLogger(__name__)

class RateLimiter:
    def __init__(self, calls_per_minute: int = 60):
        self.interval = 60.0 / calls_per_minute
        self.last_call = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self):
        async with self._lock:
            now = time.time()
            elapsed = now - self.last_call
            if elapsed < self.interval:
                await asyncio.sleep(self.interval - elapsed)
            self.last_call = time.time()

class WalletDiscovery:
    def __init__(self, client: HyperliquidClient):
        self.client = client
        self.discovered_wallets: Set[str] = set()
        self.address_pattern = re.compile(r"0x[a-fA-F0-9]{40}")
        
    async def run_discovery_cycle(self) -> List[str]:
        """Polls leaderboard to find new high-value wallets"""
        new_wallets = []
        try:
            # Poll weekly leaderboard for active traders
            leaderboard_data = await self.client.get_leaderboard("w")
            
            # Handle different potential response formats (list of dicts or list of lists)
            rows = leaderboard_data.get("leaderboardRows", []) if isinstance(leaderboard_data, dict) else leaderboard_data
            
            for row in rows:
                address = None
                if isinstance(row, dict):
                    address = row.get("window", {}).get("accountValue", {}).get("user") or row.get("address") or row.get("user")
                elif isinstance(row, list):
                    # Search for address-like string in the row
                    for item in row:
                        if isinstance(item, str) and self.address_pattern.match(item):
                            address = item
                            break
                            
                if address and address not in self.discovered_wallets:
                    self.discovered_wallets.add(address)
                    new_wallets.append(address)
            
            logger.info(f"Discovered {len(new_wallets)} new wallets")
            return new_wallets
            
        except Exception as e:
            logger.error(f"Wallet discovery failed: {e}")
            return []

class WalletTracker:
    def __init__(self, client: HyperliquidClient, rate_limit_cpm: int = 60):
        self.client = client
        self.discovery = WalletDiscovery(client)
        self.limiter = RateLimiter(rate_limit_cpm)
        
        self.tracked_wallets: Set[str] = set()
        self.high_priority_wallets: Set[str] = set() # Wallets with active high-leverage positions
        
        # Cache of latest liquidation prices: {symbol: [{price, size, side, wallet_address}]}
        self.liquidation_cache: Dict[str, List[Dict[str, Any]]] = {}
        
    async def initialize(self):
        """Initial discovery run"""
        initial_wallets = await self.discovery.run_discovery_cycle()
        self.tracked_wallets.update(initial_wallets)
        
    async def poll_liquidation_prices(self):
        """
        Polls tracked wallets for clearinghouse states to extract liquidation prices.
        Prioritizes wallets that had positions recently.
        """
        # 1. Update list occasionally
        if len(self.tracked_wallets) < 10 or datetime.utcnow().minute % 30 == 0:
             new_wallets = await self.discovery.run_discovery_cycle()
             self.tracked_wallets.update(new_wallets)
             
        # 2. Determine poll list: prioritizes 'high_priority' + random sample of others
        poll_list = list(self.high_priority_wallets)
        others = list(self.tracked_wallets - self.high_priority_wallets)
        
        # Simple sampling to respect rate limits roughly
        # We can do max ~60 calls/min. Loop takes time.
        # Let's verify ~10-20 wallets per cycle?
        if others:
            poll_list.extend(others[:10]) # Rotate this in future if needed
            
        current_liq_data: Dict[str, List[Dict]] = {}
        
        for address in poll_list:
            await self.limiter.acquire()
            user_state = await self.client.get_user_state(address)
            
            if not user_state:
                continue
                
            has_relevant_position = False
            for pos in user_state.positions:
                # 3. Extract liquidation px
                if pos.liquidation_price and pos.size > 0:
                     if pos.symbol not in current_liq_data:
                         current_liq_data[pos.symbol] = []
                     
                     current_liq_data[pos.symbol].append({
                         "price": pos.liquidation_price,
                         "size": pos.size,
                         "entry_price": pos.entry_price,
                         "leverage": pos.leverage,
                         "side": "long" if pos.side.name == "LONG" else "short",
                         "wallet": address,
                         "timestamp": datetime.utcnow()
                     })
                     
                     # Mark as high priority if high leverage or significant size
                     if pos.leverage >= 10: 
                         has_relevant_position = True
            
            # Manage priority set
            if has_relevant_position:
                self.high_priority_wallets.add(address)
            elif address in self.high_priority_wallets:
                # Remove if no longer holding relevant positions (probabilistic decay could be better but this is simple)
                self.high_priority_wallets.remove(address)

        # Update cache
        self.liquidation_cache = current_liq_data
        
    def get_liquidation_prices(self, symbol: str) -> List[Dict]:
        return self.liquidation_cache.get(symbol, [])
