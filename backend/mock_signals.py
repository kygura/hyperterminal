from __future__ import annotations

import argparse
import asyncio
import json
import random
from datetime import datetime, timezone
from itertools import count
from pathlib import Path

from models import Signal, SignalDirection, SignalMeta

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SIGNALS_PATH = PROJECT_ROOT / "data" / "signals.jsonl"


class MockSignalWriter:
    def __init__(
        self,
        output_path: Path | None = None,
        min_delay_seconds: float = 3.0,
        max_delay_seconds: float = 8.0,
    ) -> None:
        self.output_path = output_path or DEFAULT_SIGNALS_PATH
        self.min_delay_seconds = min_delay_seconds
        self.max_delay_seconds = max_delay_seconds
        self._random = random.Random()
        self._ids = count(1)

    def ensure_output(self) -> None:
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self.output_path.touch(exist_ok=True)

    def build_signal(self) -> Signal:
        asset = self._random.choice(["BTC", "ETH", "SOL", "HYPE"])
        direction = self._random.choice(
            [SignalDirection.LONG, SignalDirection.SHORT]
        )
        bid_volume = self._random.randint(4_000_000, 20_000_000)
        ask_volume = self._random.randint(4_000_000, 20_000_000)
        ratio = round(bid_volume / max(ask_volume, 1), 2)
        strength = round(self._random.uniform(0.55, 0.98), 2)
        signal_type = self._random.choice(
            [
                "orderflow_imbalance",
                "liquidity_sweep",
                "funding_shift",
                "delta_divergence",
            ]
        )

        return Signal(
            id=f"sig_{next(self._ids):06d}",
            timestamp=(
                datetime.now(timezone.utc)
                .replace(microsecond=0)
                .isoformat()
                .replace("+00:00", "Z")
            ),
            asset=asset,
            direction=direction,
            strength=strength,
            type=signal_type,
            meta=SignalMeta(
                bid_volume=bid_volume,
                ask_volume=ask_volume,
                ratio=ratio,
                timeframe=self._random.choice(["1m", "5m", "15m", "1h"]),
            ),
        )

    def write_signal(self, signal: Signal) -> dict:
        self.ensure_output()
        payload = signal.to_jsonable()

        with self.output_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload) + "\n")

        return payload

    async def run_forever(self) -> None:
        self.write_signal(self.build_signal())

        while True:
            await asyncio.sleep(
                self._random.uniform(self.min_delay_seconds, self.max_delay_seconds)
            )
            self.write_signal(self.build_signal())


async def _run_cli(args: argparse.Namespace) -> None:
    writer = MockSignalWriter(
        output_path=Path(args.output_path).resolve()
        if args.output_path
        else DEFAULT_SIGNALS_PATH,
        min_delay_seconds=args.min_delay_seconds,
        max_delay_seconds=args.max_delay_seconds,
    )

    if args.once:
        writer.write_signal(writer.build_signal())
        return

    await writer.run_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description="Write mock signals to signals.jsonl")
    parser.add_argument("--output-path", default=str(DEFAULT_SIGNALS_PATH))
    parser.add_argument("--min-delay-seconds", type=float, default=3.0)
    parser.add_argument("--max-delay-seconds", type=float, default=8.0)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    asyncio.run(_run_cli(args))


if __name__ == "__main__":
    main()
