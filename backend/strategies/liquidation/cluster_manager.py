import logging
import numpy as np
from typing import List, Dict, Optional, Tuple
from datetime import datetime, timedelta
from dataclasses import replace

from .models import LiquidationCluster, RiskConfig

logger = logging.getLogger(__name__)

class ClusterManager:
    def __init__(self, config: Dict):
        self.config = config
        self.clusters: Dict[str, List[LiquidationCluster]] = {} # symbol -> clusters
        self.half_life = config.get("cluster_half_life_min", 120)
        self.decay_lambda = np.log(2) / self.half_life
        self.min_cluster_width = 0.005 # 0.5% default width minimum
        
    def update_explicit(self, symbol: str, liq_data: List[Dict]):
        """
        Update clusters based on explicit liquidation price data from WalletTracker.
        liq_data: list of dicts {price, size, side, ...}
        """
        if not liq_data:
            return

        # Simple approach: Create one "Explicit" cluster per dense region
        # Or just one giant cluster if they are close?
        # Let's filter by side first
        long_liqs = [d for d in liq_data if d['side'] == 'long']
        short_liqs = [d for d in liq_data if d['side'] == 'short']
        
        self._process_explicit_group(symbol, long_liqs, "explicit_liq", 1.0)
        self._process_explicit_group(symbol, short_liqs, "explicit_liq", -1.0)
        
    def _process_explicit_group(self, symbol: str, data: List[Dict], c_type: str, leverage_bias: float):
        if not data:
            return
            
        prices = np.array([d['price'] for d in data])
        sizes = np.array([d['size'] * d['entry_price'] for d in data]) # Notional
        
        # Weighted avg center
        total_size = np.sum(sizes)
        if total_size == 0:
            return
            
        center = np.average(prices, weights=sizes)
        
        # Weighted std dev for width
        variance = np.average((prices - center)**2, weights=sizes)
        std_dev = np.sqrt(variance)
        
        # Width: 2 std devs or min width
        width = max(2 * std_dev, center * self.min_cluster_width)
        
        price_low = center - width/2
        price_high = center + width/2
        
        # Create new cluster
        new_cluster = LiquidationCluster(
            symbol=symbol,
            price_low=price_low,
            price_high=price_high,
            cluster_type=c_type,
            oi_density=1.0, # Explicit is high confidence
            net_leverage_bias=leverage_bias,
            estimated_liq_pressure=float(total_size),
            created_at=datetime.utcnow(),
            last_updated=datetime.utcnow(),
            decay_weight=1.0
        )
        
        self._add_or_merge_cluster(symbol, new_cluster)

    def update_inferred(self, symbol: str, underwater_bins: List[Tuple[float, float, float]]):
        """
        Update clusters based on inferred underwater OI profile.
        underwater_bins: list of (price, oi_amount, leverage_bias)
        """
        if not underwater_bins:
            return
            
        # Group adjacent bins
        # This requires a more complex binning or clustering algo (like DBSCAN 1D)
        # For simplicity, we assume pre-clustered inputs or simple merging here.
        # Let's treat the entire 'underwater' set as one or more clusters.
        
        # Sort by price
        sorted_bins = sorted(underwater_bins, key=lambda x: x[0])
        
        current_cluster_bins = []
        for bin_data in sorted_bins:
            if not current_cluster_bins:
                current_cluster_bins.append(bin_data)
                continue
                
            last_price = current_cluster_bins[-1][0]
            curr_price = bin_data[0]
            
            # If gap < X% (e.g. 1%), merge
            if (curr_price - last_price) / last_price < 0.01:
                current_cluster_bins.append(bin_data)
            else:
                # Flush current
                self._flush_inferred_cluster(symbol, current_cluster_bins)
                current_cluster_bins = [bin_data]
        
        # Flush last
        if current_cluster_bins:
            self._flush_inferred_cluster(symbol, current_cluster_bins)

    def _flush_inferred_cluster(self, symbol: str, bins: List[Tuple[float, float, float]]):
        prices = [b[0] for b in bins]
        oi_amts = [b[1] for b in bins]
        
        price_low = min(prices)
        price_high = max(prices)
        total_pressure = sum(oi_amts)
        
        # Leverage bias average
        center = np.average(prices, weights=oi_amts)
        
        # Determine bias direction (majority vote or avg)
        bias_sum = sum(b[1] * b[2] for b in bins)
        net_bias = 1.0 if bias_sum > 0 else -1.0
        
        new_cluster = LiquidationCluster(
            symbol=symbol,
            price_low=price_low,
            price_high=price_high,
            cluster_type="inferred_leverage",
            oi_density=min(1.0, total_pressure / 1_000_000), # Normalize arbitrarily? Or relative
            net_leverage_bias=net_bias,
            estimated_liq_pressure=total_pressure,
            created_at=datetime.utcnow(),
            last_updated=datetime.utcnow(),
            decay_weight=1.0
        )
        
        self._add_or_merge_cluster(symbol, new_cluster)

    def _add_or_merge_cluster(self, symbol: str, new_cluster: LiquidationCluster):
        if symbol not in self.clusters:
            self.clusters[symbol] = []
            
        # Try to merge with overlapping existing clusters
        merged = False
        for i, existing in enumerate(self.clusters[symbol]):
            # Check overlap
            overlap = max(0, min(existing.price_high, new_cluster.price_high) - max(existing.price_low, new_cluster.price_low))
            if overlap > 0:
                # Merge logic: Weighted combine
                total_pressure = existing.estimated_liq_pressure + new_cluster.estimated_liq_pressure
                w1 = existing.estimated_liq_pressure / total_pressure
                w2 = new_cluster.estimated_liq_pressure / total_pressure
                
                # New bounds (union)
                merged_low = min(existing.price_low, new_cluster.price_low)
                merged_high = max(existing.price_high, new_cluster.price_high)
                
                # Update existing
                updated = replace(existing,
                    price_low=merged_low,
                    price_high=merged_high,
                    estimated_liq_pressure=total_pressure,
                    last_updated=datetime.utcnow(),
                    decay_weight=1.0 # Reset decay on fresh activity
                )
                self.clusters[symbol][i] = updated
                merged = True
                break
        
        if not merged:
            self.clusters[symbol].append(new_cluster)

    def decay_and_prune(self):
        """Apply exponential decay and remove weak clusters"""
        now = datetime.utcnow()
        for symbol in self.clusters:
            kept = []
            for c in self.clusters[symbol]:
                age_min = (now - c.last_updated).total_seconds() / 60.0
                decay = np.exp(-self.decay_lambda * age_min)
                
                c.decay_weight = decay
                # c.estimated_liq_pressure *= decay # Decay pressure? Or just weight?
                # Usually pressure decays as price moves away or time passes
                
                if c.decay_weight > 0.1: # Prune threshold
                    kept.append(c)
            self.clusters[symbol] = kept

    def get_clusters(self, symbol: str) -> List[LiquidationCluster]:
        return self.clusters.get(symbol, [])
