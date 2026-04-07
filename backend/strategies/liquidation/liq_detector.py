import logging
from typing import Dict, Optional, List
from datetime import datetime
from dataclasses import dataclass

from .models import LiquidationEvent

logger = logging.getLogger(__name__)

@dataclass
class InferredParams:
    oi_drop_thresh_pct: float = 0.03       # 3% drop
    price_accel_thresh_pct: float = 0.015  # 1.5% move
    volume_spike_thresh: float = 3.0       # 3x volume
    funding_explain_thresh: float = 0.5    # Funding explains < 50% of move

class LiquidationDetector:
    def __init__(self, params: InferredParams = InferredParams()):
        self.params = params

    def detect_explicit(self, trade_msg: Dict) -> Optional[LiquidationEvent]:
        """
        Parse trade message for explicit liquidation flag.
        Hyperliquid specific: Look for 'liquidation' field in trade data.
        """
        # Note: Actual API field might be 'liq' or part of a bitmask.
        # Assuming standard 'liquidation' boolean or similar for now.
        if trade_msg.get("liquidation", False) or trade_msg.get("liq", False):
            return LiquidationEvent(
                symbol=trade_msg.get("coin", ""),
                side=trade_msg.get("side", "").lower(),
                price=float(trade_msg.get("px", 0)),
                size=float(trade_msg.get("sz", 0)),
                notional=float(trade_msg.get("px", 0)) * float(trade_msg.get("sz", 0)),
                timestamp=datetime.fromtimestamp(trade_msg.get("time", 0)/1000.0),
                is_explicit=True
            )
        return None

    def detect_inferred(self, symbol: str, 
                       current_price: float, 
                       price_velocity: float, # % change in last 5m
                       oi_delta_pct: float,   # % change in last 5m
                       volume_ratio: float,   # vs 24h MA
                       funding_rate: float,
                       funding_predicted: float) -> Optional[LiquidationEvent]:
        """
        Infer liquidation based on market anomalies.
        """
        
        # 1. Check OI Drop
        if abs(oi_delta_pct) < self.params.oi_drop_thresh_pct:
            return None
            
        # 2. Check Price Acceleration
        if abs(price_velocity) < self.params.price_accel_thresh_pct:
            return None
            
        # 3. Check Volume Spike
        if volume_ratio < self.params.volume_spike_thresh:
            return None
            
        # 4. Check Funding Explanation (simplified)
        # If funding is huge, OI drop might be voluntary arb unwinding? 
        # Usually checking if funding *change* explains the OI shift is complex.
        # Fallback: Just ensure direction matches.
        
        # Direction Logic:
        # Longs Liquidated: Price DOWN, OI DOWN.
        # Shorts Liquidated: Price UP, OI DOWN.
        
        is_long_liq = (price_velocity < 0) and (oi_delta_pct < 0)
        is_short_liq = (price_velocity > 0) and (oi_delta_pct < 0)
        
        if not (is_long_liq or is_short_liq):
             return None
             
        side = "buy" if is_short_liq else "sell" # Liquidator buys to cover short, or sells to close long
        # Actually 'side' usually refers to the aggressive order side.
        # If Short Liq -> Buying pressure. Side = Buy.
        # If Long Liq -> Selling pressure. Side = Sell.
        
        # Return Inferred Event
        return LiquidationEvent(
            symbol=symbol,
            side=side,
            price=current_price,
            size=0.0, # Unknown size
            notional=0.0, # Unknown notional
            timestamp=datetime.utcnow(),
            is_explicit=False
        )
