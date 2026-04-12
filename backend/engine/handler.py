import logging
import asyncio
from sqlmodel import Session, select
from db.models import Position, Trade, Wallet, WalletSnapshot, CopyConfig, PendingTrade
from db.session import engine
from engine.executor import Executor
from engine.risk import RiskManager
from datetime import datetime, timedelta

from engine.executor import Executor
from engine.risk import RiskManager

logger = logging.getLogger(__name__)

executor = Executor()
risk_manager = RiskManager()


def _coerce_leverage(value, default: int = 1) -> int:
    """Normalize leverage payloads from fills/positions into a safe integer."""
    try:
        if isinstance(value, dict):
            value = value.get("value", default)
        leverage = int(float(value))
        return leverage if leverage >= 1 else default
    except (TypeError, ValueError):
        return default


def _resolve_fill_leverage(address: str, symbol: str, fill: dict, session: Session) -> int:
    """Prefer explicit fill leverage, otherwise reuse the latest tracked open position leverage."""
    direct_leverage = fill.get("leverage")
    if direct_leverage is not None:
        return _coerce_leverage(direct_leverage)

    tracked_position = session.exec(
        select(Position)
        .where(Position.wallet_address == address)
        .where(Position.symbol == symbol)
        .where(Position.status == "OPEN")
        .order_by(Position.updated_at.desc())
        .limit(1)
    ).first()
    if tracked_position:
        return _coerce_leverage(tracked_position.leverage)

    return 1

async def handle_update(address: str, data: dict):
    if "data" not in data:
        return

    payload = data["data"]
    
    # We need a new session for each update processing to ensure thread safety with async
    with Session(engine) as session:
        if "positions" in payload:
            _handle_positions(address, payload["positions"], session)
        
        if "marginSummary" in payload:
            _handle_margin_summary(address, payload["marginSummary"], session)

        if "fills" in payload:
            await _handle_fills(address, payload["fills"], session)
            
        session.commit()

def _handle_positions(address: str, positions: list, session: Session):
    # Get all existing open positions for this wallet
    existing_positions = session.exec(
        select(Position).where(Position.wallet_address == address, Position.status == "OPEN")
    ).all()
    existing_map = {p.symbol: p for p in existing_positions}

    for pos_data in positions:
        symbol = pos_data.get("coin")
        size = float(pos_data.get("szi", 0))
        entry_price = float(pos_data.get("entryPx", 0) or 0)
        leverage = int(pos_data.get("leverage", {}).get("value", 1))
        # TODO: Calculate unrealized PnL if needed or rely on updated data

        position = existing_map.get(symbol)
        
        if size == 0:
            # Position closed
            if position:
                position.status = "CLOSED"
                position.size = 0
                session.add(position)
        else:
            # Position open/updated
            side = "LONG" if size > 0 else "SHORT"
            if position:
                # Update existing
                position.size = size
                position.entry_price = entry_price
                position.leverage = leverage
                position.side = side # Side might flip
                session.add(position)
            else:
                # Create new
                new_pos = Position(
                    wallet_address=address,
                    symbol=symbol,
                    side=side,
                    size=size,
                    entry_price=entry_price,
                    leverage=leverage,
                    status="OPEN"
                )
                session.add(new_pos)
        
        # --- RISK CHECK: TOTAL POSITION LOSS ---
        if position and position.status == "OPEN":
             # We need config
             config = session.get(CopyConfig, address)
             if not config:
                 continue
                 
             if risk_manager.check_position_loss(position, config):
                 logger.warning(f"FORCE CLOSING position {symbol} due to Max Loss Reached")
                 # Close position
                 is_buy = (position.side == "SHORT") # To close LONG, we SELL (SHORT side? No, execute_orders takes is_buy)
                 # Close Long -> Sell (is_buy=False)
                 # Close Short -> Buy (is_buy=True)
                 
                 close_buy = True if position.side == "SHORT" else False
                 
                 # Fire and forget close (async? we are in sync function here but executor is async)
                 # Wait, _handle_positions is synchronous def. executor.execute_order is async.
                 # We need to schedule it or run it. 
                 # handler.py:37 is `def _handle_positions`, called by `handle_update` which IS `async def`.
                 # So we can't await inside `_handle_positions` easily unless we change it to async.
                 # `handle_update` line 27 calls it: `_handle_positions(address, payload["positions"], session)`
                 # I should change `_handle_positions` to `async def` and await it in `handle_update`.
                 
                 # Making this change safe:
                 # I will just create a task for now to avoid blocking the DB session too much, 
                 # OR better, change `_handle_positions` to async since `handle_update` is async.
                 
                 logger.info(f"Triggering Force Close: {symbol} {'BUY' if close_buy else 'SELL'} {position.size}")
                 asyncio.create_task(executor.execute_order(symbol, close_buy, position.size, 0, "MARKET"))
                 
                 # Mark locally as closed or waiting?
                 # ideally we wait for confirmation, but for safety we can assume it will happen.
                 # We don't update DB status here, we let the fill update it next tick.


async def _handle_fills(address: str, fills: list, session: Session):
    for fill in fills:
        symbol = fill.get("coin")
        price = float(fill.get("px", 0))
        size = float(fill.get("sz", 0)) # Target size
        side = fill.get("side") # 'B' or 'S'
        side_str = "LONG" if side == "B" else "SHORT"
        leverage = _resolve_fill_leverage(address, symbol, fill, session)
        
        type_str = "LIMIT" if fill.get("crossed") else "MARKET"

        # Record the target's trade
        # Record the target's trade
        trade = Trade(
            wallet_address=address,
            symbol=symbol,
            side=side_str,
            size=size,
            price=price,
            type=type_str,
            realized_pnl=float(fill.get("closedPnl", 0) or 0),
            fee=float(fill.get("fee", 0) or 0)
        )
        session.add(trade)
        
        # --- COPY TRADING LOGIC ---
        
        # 1. Get Wallet Config
        config = session.get(CopyConfig, address)
        if not config:
            # Create default if missing (auto mode, percentage)
            config = CopyConfig(wallet_address=address)
            session.add(config)
            session.commit() # Commit to save default

        # 2a. Check Paused
        if config.is_paused:
             logger.info(f"Wallet paused, skipping execution for {symbol}")
             return

        # 2. Check Mode
        if config.mode == "manual":
             logger.info(f"Manual mode: Creating pending trade for {symbol}")
             pending = PendingTrade(
                 wallet_address=address,
                 symbol=symbol,
                 side=side_str,
                 size=size, # Store original signal size
                 leverage=leverage,
                 price=price,
                 status="PENDING"
             )
             session.add(pending)
             # session.commit() is handled at end of scope
             continue

        # 3. Calculate Size (Auto Mode)
        # We need target equity for percentage mode
        target_equity = 0.0
        latest_snap = session.exec(select(WalletSnapshot).where(WalletSnapshot.wallet_address == address).order_by(WalletSnapshot.timestamp.desc()).limit(1)).first()
        if latest_snap:
            target_equity = latest_snap.total_equity
        elif config.copy_mode == "percentage":
             # If we don't have equity history yet, we can't calculate percentage accurately
             # Fallback or skip? 
             pass
             
        our_size = risk_manager.calculate_size(size, price, target_equity, config)
        
        if our_size <= 0:
            logger.warning(f"Calculated size 0 for {symbol}, skipping copy.")
            continue
            
        # 4. Exposure Cap (1x Multiplier Check)
        # User requirement: "Exposure scaling... must be capped at 1x"
        # Since we use calculate_size which is mostly 1:1 relative or absolute, check if we inadvertently scaled up.
        # If config.exposure_cap < 1.0 (e.g. 0.5x), we apply it.
        # If it's > 1.0, we treat it as 1.0 per requirements.
        
        cap = min(config.exposure_cap, 1.0)
        our_size = our_size * cap
        
        # 5. Risk Check
        if risk_manager.validate_initial_trade(symbol, side_str, our_size, price, leverage, config):
            # 6. Execute
            logger.info(f"Copying trade for {symbol}: {side_str} {our_size}")
            is_buy = (side_str == "LONG")
            await executor.execute_order(symbol, is_buy, our_size, price, "MARKET", leverage=leverage)


async def sync_initial_orders(address: str):
    """
    Fetch existing open orders for the wallet and mirror them if they are Limit/Stop orders.
    This helps in syncing the state of a newly tracked wallet or one switched to 'auto'.
    """
    logger.info(f"Syncing initial orders for {address}")
    orders = await executor.get_open_orders(address)
    
    if not orders:
        logger.info(f"No open orders found for {address}")
        return

    for order in orders:
        # Structure of order from SDK info.open_orders usually contains:
        # {'coin': 'BTC', 'limitPx': '10000.0', 'oid': 123, 'side': 'B', 'sz': '0.1', 'timestamp': ...}
        
        # Filter: Only Limit/Stop orders (usually have a limitPx or triggerPx)
        # Hyperliquid "open orders" are generally Limit or Trigger orders.
        # We want to avoid market orders if they ever appear here (unlikely as they fill immediately).
        
        symbol = order.get("coin")
        price = float(order.get("limitPx", 0))
        size = float(order.get("sz", 0))
        side = order.get("side") # 'B' or 'A' (Ask/Sell)
        order_type = order.get("orderType", "Limit") # specific type if available

        # Skip if price is effectively 0 (market-like?) or size is 0
        if price <= 0 or size <= 0:
            continue
            
        is_buy = (side == 'B')
        
        # Scaling logic similar to _handle_touches
        scale_factor = 0.1
        our_size = size * scale_factor
        
        # Risk check
        # Passing 1x leverage approx
        side_str = "LONG" if is_buy else "SHORT"
        if risk_manager.check_trade(symbol, side_str, our_size, price, 1):
            logger.info(f"Mirroring initial order: {symbol} {side_str} {our_size} @ {price}")
            await executor.execute_order(symbol, is_buy, our_size, price, "LIMIT")

async def sync_initial_positions(address: str):
    """
    Fetch existing open positions for the wallet and store them in the database.
    This is called when a wallet is first added to capture its current state.
    """
    logger.info(f"Syncing initial positions for {address}")
    
    try:
        from data.hl_client.client import HyperliquidClient
        from data.hl_client.models import PositionSide
        
        client = HyperliquidClient()
        async with client:
            user_state = await client.get_user_state(address)
            
            if not user_state or not user_state.positions:
                logger.info(f"No positions found for {address}")
                return
            
            with Session(engine) as session:
                for position in user_state.positions:
                    # Check if position already exists
                    existing = session.exec(
                        select(Position).where(
                            Position.wallet_address == address,
                            Position.symbol == position.symbol,
                            Position.status == "OPEN"
                        )
                    ).first()
                    
                    if not existing:
                        # Create new position record
                        new_pos = Position(
                            wallet_address=address,
                            symbol=position.symbol,
                            side="LONG" if position.side == PositionSide.LONG else "SHORT",
                            size=position.size,
                            entry_price=position.entry_price,
                            leverage=int(position.leverage),
                            unrealized_pnl=position.unrealized_pnl or 0.0,
                            status="OPEN"
                        )
                        session.add(new_pos)
                        logger.info(f"Added initial position: {position.symbol} {position.side} {position.size}")
                
                session.commit()
                logger.info(f"Completed initial position sync for {address}")
                
    except Exception as e:
        logger.error(f"Failed to sync initial positions for {address}: {e}")

def _handle_margin_summary(address: str, summary: dict, session: Session):
    # summary structure: { 'accountValue': '1000.0', ... }
    try:
        total_equity = float(summary.get("accountValue", 0))
        if total_equity == 0:
            return

        # Check last snapshot to avoid spam (e.g. max 1 per hour or 1 per minute? Let's do 1 per hour for now to save space, or maybe 15 mins)
        # For real-time charts we might want more frequency, but for Daily/Weekly 60 mins is fine.
        # Let's do every 60 minutes.
        
        last_snapshot = session.exec(
            select(WalletSnapshot)
            .where(WalletSnapshot.wallet_address == address)
            .order_by(WalletSnapshot.timestamp.desc())
            .limit(1)
        ).first()

        should_create = False
        if not last_snapshot:
            should_create = True
        else:
            # Check time diff
            if datetime.utcnow() - last_snapshot.timestamp > timedelta(minutes=60):
                should_create = True
        
        if should_create:
            snapshot = WalletSnapshot(
                wallet_address=address,
                total_equity=total_equity,
                timestamp=datetime.utcnow()
            )
            session.add(snapshot)
            # session.commit() is handled by caller context
            
    except Exception as e:
        logger.error(f"Error handling margin summary for {address}: {e}")
