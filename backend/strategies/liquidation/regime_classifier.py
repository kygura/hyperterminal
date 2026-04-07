import logging
from typing import Dict, Optional
from datetime import datetime, timedelta

from .models import Regime

logger = logging.getLogger(__name__)

class RegimeClassifier:
    def __init__(self, config: Dict):
        self.config = config
        self.current_regime = Regime.NEUTRAL
        self.last_change_time = datetime.utcnow()
        self.last_regime_a_exit = datetime.min
        
        # Thresholds
        self.regime_a_enter = config.get("regime_a_threshold", 1.6)
        self.regime_a_exit = config.get("regime_a_exit", 1.0) # Hysteresis floor
        self.regime_b_cooldown = timedelta(minutes=config.get("regime_b_cooldown_min", 30))
        
    def classify(self, lvs: float, market_data: Dict) -> Regime:
        """
        Determine market regime based on LVS and state history.
        """
        now = datetime.utcnow()
        
        # 1. Momentum (Regime A) Logic with Hysteresis
        if self.current_regime == Regime.REGIME_A:
            if lvs >= self.regime_a_exit:
                return Regime.REGIME_A # Maintain A
            else:
                # Exit A
                self._transition(Regime.NEUTRAL, now)
                
        # Entry to A requires higher threshold
        elif lvs >= self.regime_a_enter:
            self._transition(Regime.REGIME_A, now)
            return Regime.REGIME_A

        # 2. Contrarian (Regime B) Logic
        # Only enter B if sufficient time passed since A (to avoid catching a falling knife)
        # And if market conditions match contrarian setup (high funding, low vol?)
        # Plan says: lvs < REGIME_A_EXIT and time_in_cluster > 10 and cooldown > 30 and funding > 1.5
        
        # Get stats
        stats = market_data.get("stats", {})
        funding_z = abs(stats.get("funding_z", 0.0))
        vol_z = stats.get("vol_z", 0.0) # Realized vol zscore
        
        time_since_a = now - self.last_regime_a_exit
        
        if (self.current_regime != Regime.REGIME_A 
            and lvs < self.regime_a_enter # Clearly not momentum
            and time_since_a > self.regime_b_cooldown
            and funding_z > 1.5
            and vol_z < 0.5): # Low vol environment
            
            if self.current_regime != Regime.REGIME_B:
                self._transition(Regime.REGIME_B, now)
            return Regime.REGIME_B
            
        # Default to Neutral if currently B but conditions invalid?
        # Or simplistic hysteresis for B too? 
        # Plan didn't specify B hysteresis, but implied exit on condition failure.
        if self.current_regime == Regime.REGIME_B:
             # Exit B if funding normalizes or Vol spikes
             if funding_z < 0.5 or vol_z > 1.0:
                  self._transition(Regime.NEUTRAL, now)
                  return Regime.NEUTRAL
             return Regime.REGIME_B

        # Fallback
        return Regime.NEUTRAL

    def _transition(self, new_regime: Regime, timestamp: datetime):
        if self.current_regime == Regime.REGIME_A and new_regime != Regime.REGIME_A:
            self.last_regime_a_exit = timestamp
            
        self.current_regime = new_regime
        self.last_change_time = timestamp
        logger.info(f"Regime transition: {self.current_regime} -> {new_regime}")
