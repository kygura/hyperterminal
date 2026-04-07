"""
Signal registry — maps signal names to their classes.
The engine uses this to instantiate signals from config.
"""

from __future__ import annotations

from signals.funding import FundingExtremesSignal
from signals.oi_volume import OIVolumeDivergenceSignal
from signals.cvd import CVDDivergenceSignal
from signals.premium import PremiumExtremesSignal
from signals.spot_led_flow import SpotLedFlowSignal
from signals.vwap_deviation import VWAPDeviationSignal

# Maps signal config filename stem → class
SIGNAL_REGISTRY: dict[str, type] = {
    "funding_extremes": FundingExtremesSignal,
    "oi_volume_divergence": OIVolumeDivergenceSignal,
    "cvd_divergence": CVDDivergenceSignal,
    "premium_extremes": PremiumExtremesSignal,
    "spot_led_flow": SpotLedFlowSignal,
    "vwap_deviation": VWAPDeviationSignal,
}

__all__ = [
    "SIGNAL_REGISTRY",
    "FundingExtremesSignal",
    "OIVolumeDivergenceSignal",
    "CVDDivergenceSignal",
    "PremiumExtremesSignal",
    "SpotLedFlowSignal",
    "VWAPDeviationSignal",
]
