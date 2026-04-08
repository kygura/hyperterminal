"""
Signal registry — maps signal names to their classes.
The engine uses this to instantiate signals from config.
"""

from __future__ import annotations

from signals.funding import FundingExtremesSignal
from signals.funding_velocity import FundingVelocitySignal
from signals.oi_volume import OIVolumeDivergenceSignal
from signals.cvd import CVDDivergenceSignal
from signals.leverage_flush import LeverageFlushSignal
from signals.liquidation_cascade import LiquidationCascadeSignal
from signals.orderbook_imbalance import OrderbookImbalanceSignal
from signals.premium import PremiumExtremesSignal
from signals.spot_led_flow import SpotLedFlowSignal
from signals.trade_flow_imbalance import TradeFlowImbalanceSignal
from signals.vwap_deviation import VWAPDeviationSignal

# Maps signal config filename stem → class
SIGNAL_REGISTRY: dict[str, type] = {
    "funding_extremes": FundingExtremesSignal,
    "funding_velocity": FundingVelocitySignal,
    "oi_volume_divergence": OIVolumeDivergenceSignal,
    "cvd_divergence": CVDDivergenceSignal,
    "leverage_flush": LeverageFlushSignal,
    "liquidation_cascade": LiquidationCascadeSignal,
    "orderbook_imbalance": OrderbookImbalanceSignal,
    "premium_extremes": PremiumExtremesSignal,
    "spot_led_flow": SpotLedFlowSignal,
    "trade_flow_imbalance": TradeFlowImbalanceSignal,
    "vwap_deviation": VWAPDeviationSignal,
}

__all__ = [
    "SIGNAL_REGISTRY",
    "FundingExtremesSignal",
    "FundingVelocitySignal",
    "OIVolumeDivergenceSignal",
    "CVDDivergenceSignal",
    "LeverageFlushSignal",
    "LiquidationCascadeSignal",
    "OrderbookImbalanceSignal",
    "PremiumExtremesSignal",
    "SpotLedFlowSignal",
    "TradeFlowImbalanceSignal",
    "VWAPDeviationSignal",
]
