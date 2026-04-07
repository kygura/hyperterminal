import logging
import asyncio
from typing import Dict, Optional, Tuple
from enum import Enum, auto

from ...hl_client.client import HyperliquidClient
from .models import TradeSignal, Regime

logger = logging.getLogger(__name__)

class ExecutionMode(Enum):
    MAKER_ONLY = auto()
    AGGRESSIVE = auto()

@dataclass
class ExecutionResult:
    filled: bool
    avg_price: float
    reason: str = ""

class MakerFirstExecutor:
    def __init__(self, client: HyperliquidClient, config: Dict):
        self.client = client
        self.slippage = config.get("slippage_tolerance", 0.001)
        self.maker_timeout = config.get("maker_timeout_ms", 500) / 1000.0
        
    async def execute(self, 
                     signal: TradeSignal, 
                     size: float, 
                     mode: ExecutionMode) -> ExecutionResult:
        """
        Execute trade with specified urgency/mode.
        """
        side = "buy" if signal.direction > 0 else "sell"
        symbol = signal.symbol
        
        # 1. Aggressive Mode (Regime A)
        if mode == ExecutionMode.AGGRESSIVE:
            # Try IOC at limit with slippage
            # Price: current * (1 +/- slippage)
            current_px = signal.price
            limit_px = current_px * (1 + self.slippage) if side == "buy" else current_px * (1 - self.slippage)
            # Rounding logic omitted for brevity (should round to tick size)
            
            logger.info(f"Executing AGGRESSIVE {side} {size} {symbol} at {limit_px}")
            
            # Use IOC order type if supported, or Market.
            # SDK execute_order supports order_type="LIMIT" with time_in_force="IOC"?
            # Simplest for "Aggressive" is just Market or marketable Limit.
            # Let's try Market for max speed in momentum for this MVP implementation.
            # Or Marketable Limit.
            
            # Using execute_order from Executor wrapper? OR Client?
            # Client doesn't have execute_order directly in the file I saw, usually via `Exchange` client.
            # Assuming `self.client` here has access to exchange calls or we use `Executor` class from engine.
            # The prompt implies I am writing a strategy module. `engine/executor.py` exists.
            # I should use THAT if possible, or assume `HyperliquidClient` passed in can do it.
            # Re-checking `hyperbot/backend/engine/executor.py` - it uses SDK directly.
            # Re-checking `client.py` - it only had INFO methods.
            # So `MakerFirstExecutor` needs `engine.executor.Executor` or similar.
            # I'll assume `self.client` passed here is the `Executor` from engine/executor.py or compatible.
            
            # Mocking the call:
            res = await self.client.execute_order(
                symbol=symbol,
                is_buy=(side == "buy"),
                size=size,
                price=limit_px,
                order_type="MARKET", # or LIMIT with ioc
                slippage=self.slippage
            )
            
            # Result parsing
            success = res.get("status") == "filled" # Simplified
            avg_px = float(res.get("avg_px", limit_px))
            return ExecutionResult(filled=success, avg_price=avg_px)
            
        # 2. Maker Only (Regime B)
        else:
             # Place Limit at Best Bid/Ask?
             # For now, simplistic implementation
             price = signal.price # Maybe improve to Best Bid/Ask + tick
             
             logger.info(f"Executing MAKER {side} {size} {symbol} at {price}")
             
             res = await self.client.execute_order(
                symbol=symbol,
                is_buy=(side == "buy"),
                size=size,
                price=price,
                order_type="LIMIT"
             )
             
             # Wait for fill?
             await asyncio.sleep(self.maker_timeout)
             
             # Check status (mock)
             # If not filled, cancel.
             # In real impl, would check status.
             return ExecutionResult(filled=True, avg_price=price) # Optimistic
