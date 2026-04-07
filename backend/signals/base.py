"""
Base classes for trading signals.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

import yaml

from data.store import DataStore

logger = logging.getLogger(__name__)


@dataclass
class SignalResult:
    signal_name: str
    coin: str
    direction: str          # "LONG_BIAS" or "SHORT_BIAS"
    strength: float         # 0.0 – 1.0
    priority: str           # "LOW", "MEDIUM", "HIGH"
    message: str            # human-readable, HTML-safe for Telegram
    timestamp: float
    metadata: dict = field(default_factory=dict)


class BaseSignal(ABC):
    """Abstract base class for all trading signals."""

    def __init__(self, name: str, config: dict, store: DataStore) -> None:
        self.name = name
        self.config = config
        self.store = store
        self.logger = logging.getLogger(f"signal.{name}")

    @abstractmethod
    def evaluate(self, coin: str) -> Optional[SignalResult]:
        """
        Evaluate the signal for a specific coin.
        Returns SignalResult or None if no signal is present.
        """
        ...

    @staticmethod
    def load_config(config_path: str) -> dict:
        """Load and return a YAML config file as a dict."""
        try:
            with open(config_path, "r") as f:
                return yaml.safe_load(f) or {}
        except FileNotFoundError:
            raise FileNotFoundError(f"Signal config not found: {config_path}")
        except yaml.YAMLError as exc:
            raise ValueError(f"Invalid YAML in {config_path}: {exc}")

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    def _map_to_priority(self, score: float, thresholds: dict) -> Optional[str]:
        """
        Map a score to priority string using thresholds dict with low/medium/high keys.
        Returns None if score is below the low threshold.
        """
        low = thresholds.get("low", 1.0)
        medium = thresholds.get("medium", 1.5)
        high = thresholds.get("high", 2.0)
        if score >= high:
            return "HIGH"
        elif score >= medium:
            return "MEDIUM"
        elif score >= low:
            return "LOW"
        return None
