from dataclasses import dataclass, field
from enum import Enum, auto
from datetime import datetime
from typing import Literal, Optional, Dict, List

class Regime(Enum):
    NEUTRAL = auto()
    REGIME_A = auto()  # Forced Liquidation Momentum
    REGIME_B = auto()  # Contrarian Mean-Reversion

@dataclass
class LiquidationCluster:
    symbol: str
    price_low: float
    price_high: float
    cluster_type: Literal["explicit_liq", "inferred_leverage"]
    oi_density: float           # Normalized OI concentration [0,1]
    net_leverage_bias: float    # [-1 (net short), +1 (net long)]
    estimated_liq_pressure: float  # Estimated cascade size in USD
    created_at: datetime
    last_updated: datetime
    decay_weight: float = 1.0   # Exponential decay factor
    
    @property
    def center_price(self) -> float:
        return (self.price_low + self.price_high) / 2.0
    
    @property
    def width(self) -> float:
        return self.price_high - self.price_low

@dataclass
class TradeSignal:
    symbol: str
    regime: Regime
    direction: int  # +1 (Long), -1 (Short)
    lvs_score: float
    cluster: Optional[LiquidationCluster]
    price: float
    timestamp: datetime
    metadata: Dict = field(default_factory=dict)

@dataclass
class RiskConfig:
    max_leverage: float = 5.0
    max_position_notional: float = 10000.0   # USD per position
    max_cluster_exposure: float = 0.3        # 30% of equity per cluster
    max_total_exposure: float = 0.6          # 60% of equity total
    max_overlap_positions: int = 2           # Max positions in overlapping clusters
    
    # Regime-specific stops
    momentum_stop_pct: float = 0.02          # 2% from entry
    momentum_time_stop_min: int = 15         # 15 minute max hold
    contrarian_stop_pct: float = 0.03        # 3% from entry (structure break)
    contrarian_time_stop_min: int = 240      # 4 hour max hold

@dataclass
class LiquidationEvent:
    symbol: str
    side: str  # "buy" or "sell" (the side of the liquidation order)
    price: float
    size: float
    notional: float
    timestamp: datetime
    is_explicit: bool = True  # True if from API, False if inferred
