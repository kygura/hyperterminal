import csv
import math
from pathlib import Path

import pytest

from data.branch_yaml import load_saved_branch_file

ROOT = Path(__file__).resolve().parents[2]
BRANCH_PATH = ROOT / "data" / "branches" / "alt_timeline_1.yaml"
CSV_FILES = {
    "BTC": ROOT / "data" / "BTCUSD_MAX_1DAY_FROM_PERPLEXITY.csv",
    "ETH": ROOT / "data" / "ETHUSD_MAX_1DAY_FROM_PERPLEXITY.csv",
    "HYPE": ROOT / "data" / "HYPEUSD_MAX_1DAY_FROM_PERPLEXITY.csv",
}


def _load_branch() -> dict:
    return load_saved_branch_file(BRANCH_PATH)["branch"]


def _load_price_series() -> dict[str, dict[str, dict[str, float]]]:
    price_map: dict[str, dict[str, dict[str, float]]] = {}
    for asset, path in CSV_FILES.items():
        with path.open("r", encoding="utf-8", newline="") as handle:
            price_map[asset] = {
                row["date"][:10]: {
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": float(row["volume"]),
                }
                for row in csv.DictReader(handle)
            }
    return price_map


def _qty(position: dict) -> float:
    effective_notional = float(position["margin"]) * float(position["leverage"])
    return effective_notional / float(position["entry_price"])


def _pnl_at(position: dict, price: float) -> float:
    direction = 1 if position["direction"] == "Long" else -1
    return _qty(position) * (price - float(position["entry_price"])) * direction


def _maintenance_margin_rate(leverage: float) -> float:
    return min(0.1, max(0.005, 0.5 / max(leverage, 1)))


def _liquidation_price(position: dict) -> float:
    leverage = float(position["leverage"])
    move = (1 - _maintenance_margin_rate(leverage)) / max(leverage, 1)
    entry_price = float(position["entry_price"])
    if position["direction"] == "Long":
        return entry_price * (1 - move)
    return entry_price * (1 + move)


def _equity_series(branch: dict, price_map: dict[str, dict[str, dict[str, float]]]) -> list[float]:
    start = str(branch["fork_date"])
    end = max(str(position["exit_date"]) for position in branch["positions"])
    timeline = sorted(
        date
        for date in price_map["BTC"].keys()
        if start <= date <= end
    )

    equity: list[float] = []
    for date in timeline:
        cash = float(branch["balance"])
        cross_upnl = 0.0
        for position in branch["positions"]:
            entry_date = str(position["entry_date"])
            exit_date = str(position["exit_date"])
            if date < entry_date:
                continue
            if date >= exit_date:
                cash += _pnl_at(position, float(position["exit_price"]))
            else:
                mark = price_map[str(position["asset"])][date]["close"]
                cross_upnl += _pnl_at(position, mark)
        equity.append(cash + cross_upnl)
    return equity


class TestAltTimelineBranch:
    def test_saved_branch_metadata(self):
        branch = _load_branch()

        assert branch["id"] == "alt_timeline_1"
        assert branch["name"] == "alt_timeline_1"
        assert float(branch["balance"]) == pytest.approx(2_500_000)
        assert len(branch["positions"]) == 4

    def test_exit_window_prices_match_seeded_trade_prices(self):
        branch = _load_branch()
        price_map = _load_price_series()

        for position in branch["positions"]:
            asset = str(position["asset"])
            entry_candle = price_map[asset][str(position["entry_date"])]
            exit_candle = price_map[asset][str(position["exit_date"])]

            assert float(position["entry_price"]) == pytest.approx(entry_candle["close"])
            assert float(position["exit_price"]) == pytest.approx(exit_candle["close"])
            assert exit_candle["low"] <= float(position["exit_price"]) <= exit_candle["high"]

    def test_positions_derive_notional_from_margin_times_leverage(self):
        branch = _load_branch()

        notionals = {
            str(position["id"]): float(position["margin"]) * float(position["leverage"])
            for position in branch["positions"]
        }

        assert notionals == pytest.approx(
            {
                "eth-apr16-jun18": 200_000,
                "eth-apr17-jul16": 200_000,
                "hype-apr15-aug06": 200_000,
                "btc-apr15-jul30": 500_000,
            }
        )
        for position in branch["positions"]:
            assert "notional" not in position

    def test_positions_reach_exit_without_liquidation(self):
        branch = _load_branch()
        price_map = _load_price_series()

        for position in branch["positions"]:
            asset = str(position["asset"])
            liq_price = _liquidation_price(position)
            lows = [
                candle["low"]
                for date, candle in sorted(price_map[asset].items())
                if str(position["entry_date"]) <= date < str(position["exit_date"])
            ]

            assert lows
            assert min(lows) > liq_price

    def test_portfolio_pnl_and_performance_metrics(self):
        branch = _load_branch()
        price_map = _load_price_series()
        realized_pnls = {
            str(position["id"]): _pnl_at(position, float(position["exit_price"]))
            for position in branch["positions"]
        }
        total_realized_pnl = sum(realized_pnls.values())
        equity = _equity_series(branch, price_map)

        peak = equity[0]
        worst_drawdown = 0.0
        for value in equity:
            peak = max(peak, value)
            worst_drawdown = min(worst_drawdown, value / peak - 1)

        returns = [equity[index] / equity[index - 1] - 1 for index in range(1, len(equity))]
        mean_return = sum(returns) / len(returns)
        variance = sum((value - mean_return) ** 2 for value in returns) / len(returns)
        sharpe = (mean_return / math.sqrt(variance)) * math.sqrt(252)

        assert realized_pnls == pytest.approx(
            {
                "eth-apr16-jun18": 120241.93701775854,
                "eth-apr17-jul16": 225944.7036613778,
                "hype-apr15-aug06": 305690.1932142621,
                "btc-apr15-jul30": 204474.82921633672,
            }
        )
        assert total_realized_pnl == pytest.approx(856351.6631097351)
        assert equity[-1] == pytest.approx(3_356_351.663109735)
        assert (equity[-1] / equity[0]) - 1 == pytest.approx(0.34254066524389404)
        assert abs(worst_drawdown) == pytest.approx(0.07619915075830885)
        assert sharpe == pytest.approx(2.2983383478132824)
