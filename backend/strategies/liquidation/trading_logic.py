import logging
import numpy as np
from typing import Dict, List, Optional
from datetime import datetime

from .models import Regime, TradeSignal, LiquidationCluster

logger = logging.getLogger(__name__)

class TradingLogic:
    def __init__(self, config: Dict):
        self.config = config
        self.regime_a_thresh = config.get("regime_a_threshold", 1.5)
        
    def check_signal(self, 
                    symbol: str, 
                    regime: Regime, 
                    market_data: Dict, 
                    active_clusters: List[LiquidationCluster], 
                    current_position: Optional[Dict]) -> Optional[TradeSignal]:
        """
        Evaluate entry conditions based on regime and return TradeSignal if valid.
        """
        if current_position:
            # We already have a position, logic is handled by 'check_exit' or risk manager usually.
            # But maybe we want to add to position? 
            # For simplicity: One position per symbol.
            return None

        current_price = market_data.get("price", 0)
        lvs = market_data.get("lvs", 0)
        stats = market_data.get("stats", {})
        
        if regime == Regime.REGIME_A:
            return self._check_momentum_entry(symbol, lvs, current_price, active_clusters, stats)
            
        elif regime == Regime.REGIME_B:
            return self._check_contrarian_entry(symbol, current_price, active_clusters, stats)
            
        return None

    def check_exit(self, 
                  symbol: str, 
                  position: Dict, 
                  regime: Regime, 
                  market_data: Dict) -> bool:
        """
        Evaluate logic-based exit conditions (separate from hard stops).
        Returns True if should exit.
        """
        lvs = market_data.get("lvs", 0)
        stats = market_data.get("stats", {})
        
        # Regime A Exits
        if regime == Regime.REGIME_A or position.get("regime") == "REGIME_A": 
            # Note: stored position metadata should indicate entry regime
            
            # 1. LVS decay
            if lvs < self.regime_a_thresh * 0.5:
                return True
                
            # 2. Cluster Exhaustion
            # If penetration > 1.2
            # Need cluster object. Stored in position? Or re-find.
            # Assuming simplified logic: If price moves significantly past entry in direction? 
            # Or reliance on LVS dropping.
            
            # 3. Volatility Collapse
            if stats.get("vol_z", 0) < -1.0:
                return True
                
        # Regime B Exits
        elif regime == Regime.REGIME_B or position.get("regime") == "REGIME_B":
            # 1. Mean Reversion Achieved
            # Target usually opposite side of cluster.
            # If PnL > target?
            
            # 2. Regime Flip
            if lvs > self.regime_a_thresh:
                 return True # Momentum taking over, abort contrarian
                 
            # 3. Funding Normalization
            funding_z = stats.get("funding_z", 0)
            side = position.get("side") # 1 or -1
            # If funding matches position side (we are paying metrics) -> Exit?
            # Originally B is counter-crowd, so we receive funding.
            # If funding_z flips, exit.
            if (side == 1 and funding_z > 0) or (side == -1 and funding_z < 0):
                return True

        return False

    def _check_momentum_entry(self, 
                             symbol: str, 
                             lvs: float, 
                             price: float, 
                             clusters: List[LiquidationCluster], 
                             stats: Dict) -> Optional[TradeSignal]:
        
        # Go WITH price velocity
        price_vel = stats.get("price_velocity", 0)
        direction = 1 if price_vel > 0 else -1
        
        # Check cluster penetration (must be inside)
        active_cluster = self._get_active_cluster(price, clusters)
        if not active_cluster:
            return None
            
        # Refined condition: cluster_penetration > X
        # Already LVS includes penetration, but explicit check ensures we are interacting with structure
        # Implementation shortcut: LVS high + Cluster present implies valid setup.
        
        return TradeSignal(
            symbol=symbol,
            regime=Regime.REGIME_A,
            direction=direction,
            lvs_score=lvs,
            cluster=active_cluster,
            price=price,
            timestamp=datetime.utcnow()
        )

    def _check_contrarian_entry(self, 
                               symbol: str, 
                               price: float, 
                               clusters: List[LiquidationCluster], 
                               stats: Dict) -> Optional[TradeSignal]:
        
        # Go AGAINST funding/crowd
        funding_rate = stats.get("funding_rate", 0)
        
        # If funding positive (Longs pay Shorts), crowd is Long. We go Short.
        # If funding negative, crowd Short. We go Long.
        direction = -1 if funding_rate > 0 else 1
        
        # Check failed attempts? (Price vs Cluster)
        # If we are Shorting, Price should be hitting Cluster High and failing?
        # Setup: price near cluster edge?
        active_cluster = self._get_active_cluster(price, clusters) # Or nearest?
        
        # For Contrarian, we might not be INSIDE, but at EDGE.
        # If no active cluster, check distance to nearest?
        if not active_cluster:
            return None
            
        return TradeSignal(
            symbol=symbol,
            regime=Regime.REGIME_B,
            direction=direction,
            lvs_score=0, # Low LVS
            cluster=active_cluster,
            price=price,
            timestamp=datetime.utcnow()
        )

    def _get_active_cluster(self, price: float, clusters: List[LiquidationCluster]) -> Optional[LiquidationCluster]:
        for c in clusters:
            # Loose bounds check
            if c.price_low * 0.99 <= price <= c.price_high * 1.01:
                return c
        return None
