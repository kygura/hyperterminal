import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, Session, create_engine, select

import api.routes as routes
import data.hl_client.client as hl_client_module
import db.session as db_session
import engine.executor as executor_module
from db.models import CopyConfig, PendingTrade, Position, Trade, Wallet, WalletSnapshot
import engine.handler as handler_module


def _build_engine(tmp_path):
    sqlite_path = tmp_path / "routes.db"
    engine = create_engine(
        f"sqlite:///{sqlite_path}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    return engine


def test_sync_historical_trades_keeps_repeated_fills_with_distinct_timestamps(tmp_path, monkeypatch):
    engine = _build_engine(tmp_path)
    monkeypatch.setattr(routes, "engine", engine)

    fills = [
        {
            "coin": "BTC",
            "side": "B",
            "sz": "0.25",
            "px": "64000",
            "closedPnl": "0",
            "fee": "1.2",
            "time": 1710000000000,
            "crossed": False,
        },
        {
            "coin": "BTC",
            "side": "B",
            "sz": "0.25",
            "px": "64000",
            "closedPnl": "0",
            "fee": "1.2",
            "time": 1710000001000,
            "crossed": False,
        },
    ]

    class FakeHLClient:
        async def get_user_fills(self, address: str):
            return fills

    monkeypatch.setattr(hl_client_module, "HyperliquidClient", FakeHLClient)

    asyncio.run(routes.sync_historical_trades("0xabc"))
    asyncio.run(routes.sync_historical_trades("0xabc"))

    with Session(engine) as session:
        trades = session.exec(
            select(Trade).where(Trade.wallet_address == "0xabc").order_by(Trade.timestamp.asc())
        ).all()

    assert len(trades) == 2
    assert len({trade.timestamp for trade in trades}) == 2


def test_manual_order_passes_leverage_to_executor(monkeypatch):
    captured = {}

    class StubExecutor:
        def __init__(self):
            return None

        async def execute_order(
            self,
            symbol,
            is_buy,
            size,
            price,
            order_type,
            **kwargs,
        ):
            captured.update(
                {
                    "symbol": symbol,
                    "is_buy": is_buy,
                    "size": size,
                    "price": price,
                    "order_type": order_type,
                    **kwargs,
                }
            )
            return {"status": "ok", "response": {"data": {"statuses": []}}}

    monkeypatch.setattr(executor_module, "Executor", StubExecutor)

    app = FastAPI()
    app.include_router(routes.router)
    client = TestClient(app)

    resp = client.post(
        "/manual_order",
        params={
            "symbol": "BTC",
            "side": "LONG",
            "size": 1.5,
            "order_type": "MARKET",
            "leverage": 7,
        },
    )

    assert resp.status_code == 200
    assert resp.json()["success"] is True
    assert captured["leverage"] == 7


def test_approve_pending_trade_passes_stored_leverage(tmp_path, monkeypatch):
    engine = _build_engine(tmp_path)
    monkeypatch.setattr(routes, "engine", engine)
    monkeypatch.setattr(db_session, "engine", engine)

    captured = {}

    class StubExecutor:
        def __init__(self):
            return None

        async def execute_order(
            self,
            symbol,
            is_buy,
            size,
            price,
            order_type,
            **kwargs,
        ):
            captured.update(
                {
                    "symbol": symbol,
                    "is_buy": is_buy,
                    "size": size,
                    "price": price,
                    "order_type": order_type,
                    **kwargs,
                }
            )
            return {"status": "ok", "response": {"data": {"statuses": []}}}

    monkeypatch.setattr(executor_module, "Executor", StubExecutor)

    with Session(engine) as session:
        trade = PendingTrade(
            wallet_address="0xabc",
            symbol="BTC",
            side="LONG",
            size=1.0,
            leverage=9,
            price=65000.0,
        )
        session.add(trade)
        session.commit()
        session.refresh(trade)
        trade_id = trade.id

    app = FastAPI()
    app.include_router(routes.router)
    client = TestClient(app)

    resp = client.post(f"/wallets/0xabc/pending_trades/{trade_id}/approve")

    assert resp.status_code == 200
    assert captured["leverage"] == 9

    with Session(engine) as session:
        stored = session.get(PendingTrade, trade_id)
        assert stored.status == "APPROVED"


def test_handle_fills_uses_tracked_position_leverage_for_pending_trade(tmp_path, monkeypatch):
    engine = _build_engine(tmp_path)
    monkeypatch.setattr(handler_module, "engine", engine)

    class StubExecutor:
        async def execute_order(self, *args, **kwargs):
            return {"status": "ok"}

        async def get_open_orders(self, *args, **kwargs):
            return []

    monkeypatch.setattr(handler_module, "executor", StubExecutor())

    with Session(engine) as session:
        session.add(Wallet(address="0xabc"))
        session.add(CopyConfig(wallet_address="0xabc", mode="manual"))
        session.add(
            Position(
                wallet_address="0xabc",
                symbol="BTC",
                side="LONG",
                size=1.0,
                entry_price=65000.0,
                leverage=7,
                status="OPEN",
            )
        )
        session.commit()

    fills = [{"coin": "BTC", "px": "65000", "sz": "0.25", "side": "B", "crossed": False}]

    with Session(engine) as session:
        asyncio.run(handler_module._handle_fills("0xabc", fills, session))
        session.commit()

    with Session(engine) as session:
        pending = session.exec(select(PendingTrade).where(PendingTrade.wallet_address == "0xabc")).one()

    assert pending.leverage == 7


def test_handle_fills_passes_tracked_position_leverage_to_auto_execution(tmp_path, monkeypatch):
    engine = _build_engine(tmp_path)
    monkeypatch.setattr(handler_module, "engine", engine)

    captured = {}

    class StubExecutor:
        async def execute_order(self, symbol, is_buy, size, price, order_type, **kwargs):
            captured.update(
                {
                    "symbol": symbol,
                    "is_buy": is_buy,
                    "size": size,
                    "price": price,
                    "order_type": order_type,
                    **kwargs,
                }
            )
            return {"status": "ok"}

        async def get_open_orders(self, *args, **kwargs):
            return []

    class StubRiskManager:
        def calculate_size(self, signal_size, signal_price, target_equity, config):
            return signal_size

        def validate_initial_trade(self, symbol, side, size, price, leverage, config):
            captured["validated_leverage"] = leverage
            return True

    monkeypatch.setattr(handler_module, "executor", StubExecutor())
    monkeypatch.setattr(handler_module, "risk_manager", StubRiskManager())

    with Session(engine) as session:
        session.add(Wallet(address="0xabc"))
        session.add(CopyConfig(wallet_address="0xabc", mode="auto", copy_mode="raw"))
        session.add(
            WalletSnapshot(
                wallet_address="0xabc",
                total_equity=10_000.0,
            )
        )
        session.add(
            Position(
                wallet_address="0xabc",
                symbol="BTC",
                side="LONG",
                size=1.0,
                entry_price=65000.0,
                leverage=6,
                status="OPEN",
            )
        )
        session.commit()

    fills = [{"coin": "BTC", "px": "65000", "sz": "0.25", "side": "B", "crossed": False}]

    with Session(engine) as session:
        asyncio.run(handler_module._handle_fills("0xabc", fills, session))
        session.commit()

    assert captured["validated_leverage"] == 6
    assert captured["leverage"] == 6


def test_read_wallets_does_not_trigger_trade_sync_side_effects(tmp_path, monkeypatch):
    engine = _build_engine(tmp_path)
    monkeypatch.setattr(routes, "engine", engine)
    monkeypatch.setattr(db_session, "engine", engine)

    calls = []

    async def fake_sync(address: str):
        calls.append(address)

    monkeypatch.setattr(routes, "sync_historical_trades", fake_sync)

    with Session(engine) as session:
        session.add(Wallet(address="0xabc", is_active=True))
        session.add(WalletSnapshot(wallet_address="0xabc", total_equity=1000.0))
        session.commit()

    with Session(engine) as session:
        response = asyncio.run(routes.read_wallets(session))

    assert len(response) == 1
    assert calls == []
