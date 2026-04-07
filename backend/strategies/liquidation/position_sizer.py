import logging
from typing import Dict
from .models import TradeSignal, LiquidationCluster, Regime, RiskConfig

logger = logging.getLogger(__name__)

class PositionSizer:
    def __init__(self, config: Dict):
        self.config = config
        
    def calculate_size(self, 
                      signal: TradeSignal, 
                      portfolio_equity: float, 
                      risk_config: RiskConfig) -> float:
        """
        Calculate suggested position size in base asset units.
        """
        if signal.price <= 0 or portfolio_equity <= 0:
            return 0.0

        # 1. Base Size (Allocation per Cluster)
        base_notional = portfolio_equity * risk_config.max_cluster_exposure
        base_size = base_notional / signal.price
        
        # If no cluster (fallback? shouldn't happen for valid signals), use base
        if not signal.cluster:
            return min(base_size, risk_config.max_position_notional / signal.price)

        cluster = signal.cluster
        width = cluster.width if cluster.width > 0 else 1.0 # Avoid div/0
        
        # 2. Adjust for Remaining Liquidation Pressure (Regime A)
        # If regime A, we want to scale by how much "fuel" is left.
        # But for Regime B, maybe we scale by reversion potential?
        # Plan specified generic logic: "(1 - penetration) * pressure"
        # We'll use LVS or penetration as proxy.
        
        # Re-calc penetration locally as signal might store it or re-derive
        dist_from_center = abs(signal.price - cluster.center_price)
        penetration = 1.0 - (dist_from_center / (width / 2)) # 1 at center, 0 at edge
        # Clamp 0..1
        penetration = max(0.0, min(1.0, penetration))
        
        pressure_factor = 1.0
        if signal.regime == Regime.REGIME_A:
            # More penetration = Less fuel left?
            # Actually Plan said: "current_penetration... estimated_remaining = pressure * (1 - penetration)"
            # So if we are at center (penetration=1), 0 fuel? That seems opposite for MOMENTUM.
            # Momentum usually peaks AT center.
            # Let's interpret Plan: "Exit on... cluster exhaustion (>1.2)".
            # Maybe "remaining pressure" means "distance left to run"?
            # If we are entering at start (penetration 0.1), lots of run left.
            # If entering at end (penetration 0.9), little run left.
            # So (1 - penetration) makes sense.
            pressure_factor = 1.0 - penetration
            pressure_factor = max(0.2, pressure_factor) # Floor at 20%
        
        # 3. Distance to Edge (Safety)
        # "Edge Adjusted"
        edge_adjusted = base_size * pressure_factor
        
        # 4. Regime Multiplier
        regime_mult = 1.0 if signal.regime == Regime.REGIME_A else 0.5
        
        final_size = edge_adjusted * regime_mult
        
        # 5. Cap at Max Notional
        max_size = risk_config.max_position_notional / signal.price
        final_size = min(final_size, max_size)
        
        return final_size
