import numpy as np
from typing import Dict, List
from datetime import datetime

from .models import LiquidationCluster

class LVSCalculator:
    def __init__(self, config: Dict):
        self.weights = config.get("lvs_weights", {
            "price_acceleration": 0.25,
            "volume_spike": 0.20,
            "liquidation_intensity": 0.30,
            "cluster_penetration": 0.20,
            "time_penalty": 0.10
        })
        
    def calculate(self, symbol: str, market_data: Dict, clusters: List[LiquidationCluster]) -> float:
        """
        Calculate Liquidation Velocity Score (LVS).
        market_data: {price, recent_trades, funding_history, ...}
        """
        current_price = market_data.get("price", 0)
        if current_price == 0:
            return 0.0

        # 1. Price Acceleration Z-Score
        # Retrieve pre-computed or compute here. 
        # Assuming market_data has normalized stats or we compute on fly.
        # Let's assume passed market_data contains 'stats' dict with zscores.
        stats = market_data.get("stats", {})
        price_accel_z = stats.get("price_accel_z", 0.0)
        
        # 2. Volume Spike Z-Score
        vol_spike_z = stats.get("vol_spike_z", 0.0)
        
        # 3. Liquidation Intensity Z-Score
        liq_intensity_z = stats.get("liq_intensity_z", 0.0)
        
        # 4. Cluster Penetration Score
        # Max penetration across active clusters
        cluster_score = 0.0
        active_cluster = None
        
        for cluster in clusters:
            p_score = self._calculate_penetration(current_price, cluster)
            if p_score > cluster_score:
                cluster_score = p_score
                active_cluster = cluster
                
        # 5. Time Penalty (if in cluster)
        time_penalty = 0.0
        if active_cluster:
            minutes_in = (datetime.utcnow() - active_cluster.last_updated).total_seconds() / 60.0
            time_penalty = min(1.0, minutes_in / 60.0) # Cap at 1 hour
            
        # Composite Score
        lvs = (
            self.weights["price_acceleration"] * price_accel_z +
            self.weights["volume_spike"] * vol_spike_z +
            self.weights["liquidation_intensity"] * liq_intensity_z +
            self.weights["cluster_penetration"] * cluster_score -
            self.weights["time_penalty"] * time_penalty
        )
        
        return lvs

    def _calculate_penetration(self, price: float, cluster: LiquidationCluster) -> float:
        """
        Score 0..1+ based on how deep into cluster price is.
        """
        width = cluster.width
        if width == 0: return 0.0
        
        center = cluster.center_price
        
        # Normalized distance from center (0 at center, 1 at edge)
        dist_from_center = abs(price - center) / (width / 2)
        
        # We want higher score as we penetration THROUGH it? 
        # Plan says: 
        # if price < low: (low - price)/width ... (approaching from below?)
        # Actually penetration usually means 'Inside'.
        # Plan: "How deep into cluster has price moved"
        # If inside: 0.5 * (1 - dist) * density
        # If through (broken): score > 1 ?
        
        # Let's align with plan logic roughly:
        if cluster.price_low <= price <= cluster.price_high:
            # Inside: score peaks at center
            return 0.5 * (1.0 - dist_from_center) * cluster.oi_density
        else:
             # Outside
             # If strictly penetration means 'passing through', we need history.
             # Static view: if we are BEYOND it, is that penetration?
             # Strategy says: "Enter on liquidation acceleration INSIDE cluster" (Regime A).
             # So we care about being INSIDE.
             return 0.0 
