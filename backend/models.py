from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class SignalDirection(str, Enum):
    LONG = "Long"
    SHORT = "Short"


class SignalMeta(BaseModel):
    bid_volume: int = Field(..., ge=0)
    ask_volume: int = Field(..., ge=0)
    ratio: float = Field(..., ge=0)
    timeframe: str = Field(..., min_length=1)


class Signal(BaseModel):
    id: str = Field(..., min_length=1)
    timestamp: str = Field(..., min_length=1)
    asset: str = Field(..., min_length=1, max_length=10)
    direction: SignalDirection
    strength: float = Field(..., ge=0, le=1)
    type: str = Field(..., min_length=1)
    meta: SignalMeta

    def to_jsonable(self) -> dict[str, Any]:
        if hasattr(self, "model_dump"):
            data = self.model_dump()
        else:
            data = self.dict()

        if isinstance(data.get("direction"), Enum):
            data["direction"] = data["direction"].value

        return data


def parse_signal_json(raw: str) -> Signal:
    if hasattr(Signal, "model_validate_json"):
        return Signal.model_validate_json(raw)
    return Signal.parse_raw(raw)
