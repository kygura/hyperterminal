import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from .models import Regime, TradeSignal, LiquidationCluster, RiskConfig

logger = logging.getLogger(__name__)

class RiskManager:
    def __init__(self, config: Dict):
        self.config = config
        
    def get_stop_price(self, 
                      entry_price: float, 
                      direction: int, 
                      regime: Regime, 
                      cluster: Optional[LiquidationCluster], 
                      risk_config: RiskConfig) -> float:
        """
        Calculate stop loss price based on regime rules.
        """
        if regime == Regime.REGIME_A:
            # Momentum: Tight pct stop
            # Plan: "Time/vol... momentum_stop_pct"
            stop_dist = entry_price * risk_config.momentum_stop_pct
            return entry_price - (direction * stop_dist)
            
        elif regime == Regime.REGIME_B:
            # Contrarian: Structure invalidation
            # "Stop beyond cluster edge"
            if not cluster:
                # Fallback to pct
                stop_dist = entry_price * risk_config.contrarian_stop_pct
                return entry_price - (direction * stop_dist)
            
            if direction > 0: # Long -> Stop below Low
                structure_stop = cluster.price_low * 0.995
                # Also apply max pct stop
                pct_stop = entry_price * (1 - risk_config.contrarian_stop_pct)
                return max(structure_stop, pct_stop) # Tighter of the two? Or looser?
                # Usually Stop is max(...) for LONG (higher price is closer to entry if both below)
                # But here we want the "Structure Invalidation". If structure is -5% away, and max_stop is -3%, we should probably take -3% (hard risk limit).
                # So take MAX of (structure_stop, pct_stop_price).
                
            else: # Short -> Stop above High
                structure_stop = cluster.price_high * 1.005
                pct_stop = entry_price * (1 + risk_config.contrarian_stop_pct)
                return min(structure_stop, pct_stop)
                
        return 0.0

    def check_exposure_limits(self, 
                             new_notional: float, 
                             current_positions: List[Dict], 
                             portfolio_equity: float, 
                             risk_config: RiskConfig) -> bool:
        """
        Check if new trade violates total exposure limits.
        """
        current_exposure = sum(
            abs(p['size'] * p['price']) for p in current_positions
        )
        total_exposure = current_exposure + new_notional
        
        if total_exposure > (portfolio_equity * risk_config.max_total_exposure):
            logger.warning("Risk Check Failed: Max Total Exposure Exceeded")
            return False
            
        return True
