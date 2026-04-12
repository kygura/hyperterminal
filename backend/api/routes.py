from fastapi import APIRouter, Depends, HTTPException
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlmodel import Session, select, func
from typing import List
from db.session import get_session, engine
from db.models import Wallet, Position, Trade, Goal, CopyConfig, PendingTrade, AllocatedPosition
from engine.handler import handle_update, sync_initial_orders, sync_initial_positions
from engine.watcher import watcher
from engine.watcher import watcher
import asyncio
from typing import Optional, Dict
from pydantic import BaseModel
from datetime import datetime, timedelta
from db.models import WalletSnapshot
import logging

logger = logging.getLogger(__name__)


router = APIRouter()


class PnLMetric(BaseModel):
    amount: float
    percentage: float

class WalletMetrics(BaseModel):
    dailyPnL: PnLMetric
    weeklyPnL: PnLMetric
    monthlyPnL: PnLMetric
    allTimePnL: PnLMetric
    allTimePnL: PnLMetric
    totalEquity: float = 0.0

class PerformanceMetrics(BaseModel):
    totalPnl: float = 0.0
    totalPnlPercent: float = 0.0
    maxDrawdown: float = 0.0
    maxDrawdownPercent: float = 0.0
    winRate: float = 0.0
    avgHoldingTime: float = 0.0
    totalTrades: int = 0
    winningTrades: int = 0
    losingTrades: int = 0
    fundingPnl: float = 0.0
    sharpeRatio: float = 0.0

class WalletResponse(BaseModel):
    address: str
    name: Optional[str] = None
    is_active: bool
    created_at: datetime
    metrics: Optional[WalletMetrics] = None
    performance: Optional[PerformanceMetrics] = None
    mode: str = "manual" # Helper to elevate the mode string
    copy_config: Optional[CopyConfig] = None
    positions: List[Dict] = []  # For risk assessment

    class Config:
        from_attributes = True


def _normalized_trade_number(value) -> float:
    return round(float(value or 0), 12)


def _stored_trade_signature(trade: Trade) -> tuple:
    trade_time_ms = int(trade.timestamp.timestamp() * 1000) if trade.timestamp else 0
    return (
        trade.symbol,
        trade.side,
        _normalized_trade_number(trade.size),
        _normalized_trade_number(trade.price),
        _normalized_trade_number(trade.realized_pnl),
        _normalized_trade_number(trade.fee),
        trade_time_ms,
    )


def _fill_trade_signature(fill: dict) -> tuple:
    side = "LONG" if fill.get("side") == "B" else "SHORT"
    return (
        fill.get("coin"),
        side,
        _normalized_trade_number(fill.get("sz", 0)),
        _normalized_trade_number(fill.get("px", 0)),
        _normalized_trade_number(fill.get("closedPnl", 0)),
        _normalized_trade_number(fill.get("fee", 0)),
        int(fill.get("time", 0) or 0),
    )

async def calculate_pnl_metrics(wallet: Wallet, session: Session) -> WalletMetrics:
    # Get latest snapshot (current state)
    latest = session.exec(
        select(WalletSnapshot)
        .where(WalletSnapshot.wallet_address == wallet.address)
        .order_by(WalletSnapshot.timestamp.desc())
        .limit(1)
    ).first()

    if not latest:
        # Try to fetch live data from Hyperliquid as fallback
        try:
            from data.hl_client.client import HyperliquidClient
            client = HyperliquidClient()
            info = await client.get_user_state(wallet.address)
            if info and info.balance:
                current_equity = float(info.balance)
                zero = PnLMetric(amount=0, percentage=0)
                print(f"[INFO] Using live data for {wallet.address}: ${current_equity:,.2f}")
                return WalletMetrics(
                    dailyPnL=zero,
                    weeklyPnL=zero,
                    monthlyPnL=zero,
                    allTimePnL=zero,
                    totalEquity=current_equity
                )
        except Exception as e:
            print(f"[WARN] Failed to fetch live data: {e}")
        
        # Final fallback to zeros
        zero = PnLMetric(amount=0, percentage=0)
        return WalletMetrics(
            dailyPnL=zero, 
            weeklyPnL=zero, 
            monthlyPnL=zero, 
            allTimePnL=zero,
            totalEquity=0.0
        )

    current_equity = latest.total_equity

    def get_past_equity(delta: timedelta) -> float:
        past_time = datetime.utcnow() - delta
        snapshot = session.exec(
            select(WalletSnapshot)
            .where(WalletSnapshot.wallet_address == wallet.address)
            .where(WalletSnapshot.timestamp <= past_time)
            .order_by(WalletSnapshot.timestamp.desc())
            .limit(1)
        ).first()
        
        if not snapshot:
             # Try to find the earliest snapshot EVER
            earliest = session.exec(
                select(WalletSnapshot)
                .where(WalletSnapshot.wallet_address == wallet.address)
                .order_by(WalletSnapshot.timestamp.asc())
                .limit(1)
            ).first()
            if earliest and earliest.timestamp > past_time:
                return earliest.total_equity
            return current_equity # Fallback: no change
            
        return snapshot.total_equity

    def calc_metric(past_equity: float) -> PnLMetric:
        if past_equity == 0:
            return PnLMetric(amount=0, percentage=0)
        diff = current_equity - past_equity
        percent = (diff / past_equity) * 100
        return PnLMetric(amount=diff, percentage=percent)

    return WalletMetrics(
        dailyPnL=calc_metric(get_past_equity(timedelta(days=1))),
        weeklyPnL=calc_metric(get_past_equity(timedelta(days=7))),
        monthlyPnL=calc_metric(get_past_equity(timedelta(days=30))),
        allTimePnL=calc_metric(get_past_equity(timedelta(days=365*10))),
        totalEquity=current_equity
    )

def calculate_performance(wallet: Wallet, session: Session) -> PerformanceMetrics:
    trades = session.exec(select(Trade).where(Trade.wallet_address == wallet.address)).all()
    
    total_trades = len(trades)
    winning = 0
    losing = 0
    total_pnl = 0.0
    
    for t in trades:
        pnl = t.realized_pnl
        total_pnl += pnl
        if pnl > 0:
            winning += 1
        elif pnl < 0:
            losing += 1
            
    win_rate = (winning / total_trades * 100) if total_trades > 0 else 0.0
    
    # Calculate Max Drawdown from Snapshots
    max_drawdown = 0.0
    max_dd_percent = 0.0
    
    snapshots = session.exec(select(WalletSnapshot).where(WalletSnapshot.wallet_address == wallet.address).order_by(WalletSnapshot.timestamp.asc())).all()
    
    initial_equity = snapshots[0].total_equity if snapshots else 0.0
    current_equity = snapshots[-1].total_equity if snapshots else 0.0
    
    if snapshots:
        peak = -999999999.0
        for s in snapshots:
            if s.total_equity > peak:
                peak = s.total_equity
            dd = peak - s.total_equity
            if dd > max_drawdown:
                max_drawdown = dd
                max_dd_percent = (dd / peak * 100) if peak > 0 else 0
                
    total_pnl_percent = 0.0
    if initial_equity > 0:
         total_pnl_percent = ((current_equity - initial_equity) / initial_equity) * 100
         
    return PerformanceMetrics(
        totalPnl=total_pnl,
        totalPnlPercent=total_pnl_percent,
        maxDrawdown=max_drawdown,
        maxDrawdownPercent=max_dd_percent,
        winRate=win_rate,
        totalTrades=total_trades,
        winningTrades=winning,
        losingTrades=losing,
        fundingPnl=0.0,
        sharpeRatio=0.0 # Complex calc, skip for now
    )

async def sync_historical_trades(address: str):
    """Fetch and sync historical trades (fills) from Hyperliquid"""
    try:
        from data.hl_client.client import HyperliquidClient
        client = HyperliquidClient()
        fills = await client.get_user_fills(address)
        
        with Session(engine) as session:
            # Get existing signatures to avoid duplicates
            current_trades = session.exec(select(Trade).where(Trade.wallet_address == address)).all()
            existing_sigs = {_stored_trade_signature(t) for t in current_trades}
                
            count = 0
            for fill in fills:
                 symbol = fill.get("coin")
                 price = float(fill.get("px", 0))
                 size = float(fill.get("sz", 0))
                 side = "LONG" if fill.get("side") == "B" else "SHORT"
                 signature = _fill_trade_signature(fill)
                  
                 if signature in existing_sigs:
                     continue
                      
                 # Create Trade
                 trade = Trade(
                     wallet_address=address,
                     symbol=symbol,
                     side=side,
                     size=size,
                     price=price,
                     type="LIMIT" if fill.get("crossed") else "MARKET",
                     realized_pnl=float(fill.get("closedPnl", 0) or 0),
                     fee=float(fill.get("fee", 0) or 0),
                     timestamp=datetime.fromtimestamp(fill.get("time", 0)/1000)
                 )
                 session.add(trade)
                 existing_sigs.add(signature)
                 count += 1
            
            if count > 0:
                session.commit()
                print(f"[INFO] Synced {count} historical trades for {address}")
                
    except Exception as e:
        print(f"[ERROR] Historical sync failed for {address}: {e}")

async def create_initial_snapshot(wallet_address: str):
    """Fetch current state from Hyperliquid and create initial snapshot"""
    try:
        from data.hl_client.client import HyperliquidClient
        client = HyperliquidClient()
        info = await client.get_user_state(wallet_address)
        if info and info.balance:
            equity = float(info.balance)
            
            with Session(engine) as session:
                snapshot = WalletSnapshot(
                    wallet_address=wallet_address,
                    total_equity=equity
                )
                session.add(snapshot)
                session.commit()
                print(f"[INFO] Created initial snapshot for {wallet_address}: ${equity:,.2f}")
    except Exception as e:
        print(f"[ERROR] Failed to create initial snapshot for {wallet_address}: {e}")

@router.post("/wallets/", response_model=Wallet)
async def create_wallet(wallet: Wallet):
    with Session(engine) as session: # Assuming 'engine' is defined

        existing = session.exec(select(Wallet).where(Wallet.address == wallet.address)).first()
        if existing:
            # If reactivating or updating, we might want to check mode too
            existing.is_active = True
            if wallet.name:
                existing.name = wallet.name
            session.add(existing)
            session.commit()
            session.refresh(existing)
            
            # Subscribe to updates
            await watcher.subscribe_to_user(existing.address)
            return existing
            
        session.add(wallet)
        session.commit()
        session.refresh(wallet)
        
        # Subscribe to updates
        await watcher.subscribe_to_user(wallet.address)
        
        # Initialize default CopyConfig if not exists
        if not wallet.copy_config:
            default_config = CopyConfig(wallet_address=wallet.address)
            session.add(default_config)
            session.commit()
            session.refresh(wallet)

        # Trigger initial sync
        asyncio.create_task(sync_initial_orders(wallet.address))
        
        # Sync initial positions
        asyncio.create_task(sync_initial_positions(wallet.address))
        
        # Trigger historical trade sync
        asyncio.create_task(sync_historical_trades(wallet.address))
        
        # Create initial snapshot
        asyncio.create_task(create_initial_snapshot(wallet.address))

        return wallet

@router.get("/wallets/", response_model=List[WalletResponse])
async def read_wallets(session: Session = Depends(get_session)):
    wallets = session.exec(select(Wallet)).all()
    response = []
    for w in wallets:
        metrics = await calculate_pnl_metrics(w, session)
        perf = calculate_performance(w, session)

        # Ensure config exists (lazy load or just handle potential None)
        # If None, maybe we should create one or just return None (frontend handles default)
        cfg = w.copy_config
        
        # Fetch positions for this wallet
        from db.models import Position
        positions = session.exec(
            select(Position).where(Position.wallet_address == w.address).where(Position.status == "OPEN")
        ).all()
        
        response.append(WalletResponse(
            address=w.address,
            name=w.name,
            is_active=w.is_active,
            created_at=w.created_at,
            metrics=metrics,
            performance=perf,
            mode=cfg.mode if cfg else "manual",
            copy_config=cfg,
            positions=[{
                "symbol": p.symbol,
                "side": p.side,
                "size": p.size,
                "entry_price": p.entry_price,
                "leverage": p.leverage,
                "unrealized_pnl": p.unrealized_pnl
            } for p in positions]
        ))
    return response

@router.get("/prices")
async def get_current_prices():
    """Get current asset prices from Hyperliquid"""
    try:
        from data.hl_client.client import HyperliquidClient
        client = HyperliquidClient()
        all_mids = await client.get_all_mids()
        
        assets = ["BTC", "ETH", "SOL", "HYPE"]
        prices = {}
        
        for asset in assets:
            # Try with USD suffix first (common format)
            price = all_mids.get(f"{asset}USD") or all_mids.get(asset)
            if price:
                prices[asset] = float(price)
            else:
                prices[asset] = 0.0
        
        return prices
    except Exception as e:
        print(f"[ERROR] Failed to fetch prices: {e}")
        # Return zeros as fallback
        return {"BTC": 0.0, "ETH": 0.0, "SOL": 0.0, "HYPE": 0.0}

@router.get("/wallets/{address}", response_model=Wallet)
def read_wallet(address: str, session: Session = Depends(get_session)):
    wallet = session.get(Wallet, address)
    if not wallet:
        raise HTTPException(status_code=404, detail="Wallet not found")
    return wallet

@router.delete("/wallets/{address}")
def delete_wallet(address: str, session: Session = Depends(get_session)):
    wallet = session.get(Wallet, address)
    if not wallet:
        raise HTTPException(status_code=404, detail="Wallet not found")
    session.delete(wallet)
    session.commit()
    return {"ok": True}

@router.get("/config")
def get_public_config():
    """Get public configuration including vault address"""
    from core.settings import settings
    return {
        "vault_address": settings.hyperliquid.vault_address,
        "agent_address": settings.hyperliquid.agent_wallet_address, # Optional, but might be useful
        "api_url": settings.hyperliquid.api_url
    }

@router.get("/open_orders")
async def get_aggregated_open_orders(session: Session = Depends(get_session)):
    """Get aggregated open orders from all active tracked wallets"""
    from engine.executor import Executor
    
    # Get all active wallets
    wallets = session.exec(select(Wallet).where(Wallet.is_active == True)).all()
    
    executor = Executor()
    all_orders = []
    
    for wallet in wallets:
        try:
            orders = await executor.get_open_orders(wallet.address)
            if orders:
                # Add wallet info to each order
                for order in orders:
                    order['wallet_address'] = wallet.address
                    order['wallet_name'] = wallet.name or f"{wallet.address[:8]}..."
                all_orders.extend(orders)
        except Exception as e:
            logger.error(f"Failed to fetch orders for {wallet.address}: {e}")
            continue
    
    return all_orders

@router.post("/manual_order")
async def place_manual_order(
    symbol: str,
    side: str,  # "LONG" or "SHORT"
    size: float,
    order_type: str,  # "MARKET" or "LIMIT"
    limit_price: float | None = None,
    leverage: int = 1,
):
    """
    Place a manual order on the vault wallet using the agent wallet.
    This uses the executor which is configured with agent credentials.
    """
    from engine.executor import Executor

    if leverage < 1:
        raise HTTPException(status_code=422, detail="leverage must be at least 1x")
    
    # Convert side to boolean (is_buy)
    is_buy = (side.upper() == "LONG")
    
    # Use limit_price if provided, otherwise 0 for market orders
    price = limit_price if order_type.upper() == "LIMIT" and limit_price else 0
    
    executor = Executor()
    
    try:
        result = await executor.execute_order(
            symbol=symbol,
            is_buy=is_buy,
            size=size,
            price=price,
            order_type=order_type.upper(),
            leverage=leverage,
        )

        if result.get("status") != "ok":
            logger.error(f"Manual order failed: {result}")
            return {
                "success": False,
                "error": result.get("error", "Order execution failed"),
                "result": result,
            }
        
        logger.info(f"Manual order placed: {symbol} {side} {size} @ {price} (leverage: {leverage}x)")
        
        return {
            "success": True,
            "order": {
                "symbol": symbol,
                "side": side,
                "size": size,
                "order_type": order_type,
                "limit_price": limit_price,
                "leverage": leverage,
            },
            "result": result
        }
    except Exception as e:
        logger.error(f"Failed to place manual order: {e}")
        return {
            "success": False,
            "error": str(e)
        }


# --- GOALS ---

@router.get("/goals/", response_model=List[Goal])
def read_goals(session: Session = Depends(get_session)):
    return session.exec(select(Goal)).all()

    session.refresh(goal)
    return goal

# --- CONFIG & PENDING TRADES ---

@router.get("/wallets/{address}/config", response_model=CopyConfig)
def get_wallet_config(address: str, session: Session = Depends(get_session)):
    config = session.get(CopyConfig, address)
    if not config:
        # Create default if missing for some reason
        wallet = session.get(Wallet, address)
        if not wallet:
            raise HTTPException(status_code=404, detail="Wallet not found")
        config = CopyConfig(wallet_address=address)
        session.add(config)
        session.commit()
        session.refresh(config)
    return config

@router.put("/wallets/{address}/config", response_model=CopyConfig)
def update_wallet_config(address: str, config_update: CopyConfig, session: Session = Depends(get_session)):
    config = session.get(CopyConfig, address)
    if not config:
        # Create if missing
         wallet = session.get(Wallet, address)
         if not wallet:
            raise HTTPException(status_code=404, detail="Wallet not found")
         config = CopyConfig(wallet_address=address)
         session.add(config)
    
    # Update fields
    config.mode = config_update.mode
    config.is_paused = config_update.is_paused
    config.copy_mode = config_update.copy_mode
    config.allocation_amount = config_update.allocation_amount
    config.max_position_loss = config_update.max_position_loss
    config.max_position_loss_type = config_update.max_position_loss_type
    config.exposure_cap = config_update.exposure_cap
    config.max_leverage = config_update.max_leverage
    
    session.add(config)
    session.commit()
    session.refresh(config)
    return config

@router.get("/wallets/{address}/pending_trades", response_model=List[PendingTrade])
def get_pending_trades(address: str, session: Session = Depends(get_session)):
    return session.exec(select(PendingTrade).where(PendingTrade.wallet_address == address, PendingTrade.status == "PENDING")).all()

@router.post("/wallets/{address}/pending_trades/{trade_id}/approve")
async def approve_pending_trade(address: str, trade_id: int, session: Session = Depends(get_session)):
    trade = session.get(PendingTrade, trade_id)
    if not trade or trade.wallet_address != address:
        raise HTTPException(status_code=404, detail="Trade not found")
    
    if trade.status != "PENDING":
        raise HTTPException(status_code=400, detail="Trade not pending")
    
    # Execute immediately
    from engine.executor import Executor
    executor = Executor()
    
    # Determine side boolean
    is_buy = (trade.side == "LONG")
    
    result = await executor.execute_order(
        trade.symbol,
        is_buy,
        trade.size,
        trade.price,
        "MARKET",
        leverage=trade.leverage,
    )
    if result.get("status") != "ok":
        raise HTTPException(status_code=502, detail=result.get("error", "Trade execution failed"))

    # Mark as approved only after the execution path succeeds.
    trade.status = "APPROVED"
    session.add(trade)
    session.commit()
    
    return {"status": "executed", "result": result}

class PendingTradeUpdate(BaseModel):
    size: Optional[float] = None
    leverage: Optional[int] = None
    price: Optional[float] = None

@router.put("/wallets/{address}/pending_trades/{trade_id}", response_model=PendingTrade)
def update_pending_trade(address: str, trade_id: int, updates: PendingTradeUpdate, session: Session = Depends(get_session)):
    trade = session.get(PendingTrade, trade_id)
    if not trade or trade.wallet_address != address:
        raise HTTPException(status_code=404, detail="Trade not found")
    
    if trade.status != "PENDING":
        raise HTTPException(status_code=400, detail="Can only edit pending trades")

    if updates.size is not None:
        trade.size = updates.size
    if updates.leverage is not None:
        trade.leverage = updates.leverage
    if updates.price is not None:
        trade.price = updates.price
        
    session.add(trade)
    session.commit()
    session.refresh(trade)
    return trade

@router.post("/wallets/{address}/pending_trades/{trade_id}/reject")
def reject_pending_trade(address: str, trade_id: int, session: Session = Depends(get_session)):
    trade = session.get(PendingTrade, trade_id)
    if not trade or trade.wallet_address != address:
        raise HTTPException(status_code=404, detail="Trade not found")
        
    if trade.status != "PENDING":
        raise HTTPException(status_code=400, detail="Trade not pending")
        
    trade.status = "REJECTED"
    session.add(trade)
    session.commit()
    return {"status": "rejected"}


@router.get("/goal")
def get_goal(session: Session = Depends(get_session)):
    """Get user's goal settings"""
    from db.models import UserSettings
    import json
    
    setting = session.exec(
        select(UserSettings).where(UserSettings.setting_key == "goal")
    ).first()
    
    if setting:
        return json.loads(setting.setting_value)
    
    # Return default
    return {
        "targetCapital": 100000,
        "currentProgress": 0,
        "requiredMultiple": 1
    }

@router.put("/goal")
def update_goal(goal: dict, session: Session = Depends(get_session)):
    """Update user's goal"""
    from db.models import UserSettings
    import json
    
    setting = session.exec(
        select(UserSettings).where(UserSettings.setting_key == "goal")
    ).first()
    
    value = json.dumps(goal)
    
    if setting:
        setting.setting_value = value
        setting.updated_at = datetime.utcnow()
    else:
        setting = UserSettings(setting_key="goal", setting_value=value)
        session.add(setting)
    
    session.commit()
    return goal

# --- ALLOCATED POSITIONS (Capital Allocation Simulator) ---

class AllocatedPositionCreate(BaseModel):
    id: str
    asset: str
    side: str
    size: float
    leverage: int
    margin: float
    entry_price: float
    entry_timestamp: int
    exit_price: Optional[float] = None
    exit_timestamp: Optional[int] = None
    status: str = "open"
    adjustment_reason: Optional[str] = None

@router.get("/allocated_positions/", response_model=List[AllocatedPosition])
def get_allocated_positions(session: Session = Depends(get_session)):
    """Get all allocated positions"""
    return session.exec(select(AllocatedPosition).order_by(AllocatedPosition.created_at.desc())).all()

@router.post("/allocated_positions/bulk")
def save_allocated_positions(
    positions: List[AllocatedPositionCreate],
    session: Session = Depends(get_session)
):
    """Save or update multiple allocated positions"""
    saved = []
    
    for pos_data in positions:
        # Check if position exists
        existing = session.get(AllocatedPosition, pos_data.id)
        
        if existing:
            # Update existing
            for key, value in pos_data.dict().items():
                setattr(existing, key, value)
            existing.updated_at = datetime.utcnow()
            session.add(existing)
            saved.append(existing)
        else:
            # Create new
            new_pos = AllocatedPosition(**pos_data.dict())
            session.add(new_pos)
            saved.append(new_pos)
    
    session.commit()
    return {"saved": len(saved), "positions": saved}

@router.delete("/allocated_positions/{position_id}")
def delete_allocated_position(
    position_id: str,
    session: Session = Depends(get_session)
):
    """Delete an allocated position"""
    position = session.get(AllocatedPosition, position_id)
    if not position:
        raise HTTPException(status_code=404, detail="Position not found")
    
    session.delete(position)
    session.commit()
    return {"ok": True}

@router.delete("/allocated_positions/")
def clear_allocated_positions(session: Session = Depends(get_session)):
    """Clear all allocated positions"""
    positions = session.exec(select(AllocatedPosition)).all()
    for pos in positions:
        session.delete(pos)
    session.commit()
    return {"deleted": len(positions)}

@router.get("/hyperliquid/leverage_limits")
async def get_leverage_limits():
    """Fetch live leverage limits from Hyperliquid meta API"""
    try:
        from data.hl_client.client import HyperliquidClient
        client = HyperliquidClient()
        
        # Fetch metadata
        meta, _ = await client.get_meta_and_asset_ctxs()
        
        if not meta:
            raise HTTPException(status_code=500, detail="Failed to fetch metadata")
        
        universe = meta.get("universe", [])
        leverage_limits = {}
        
        for asset_meta in universe:
            symbol = asset_meta.get("name")
            if not symbol:
                continue
            
            # Get margin tiers (IMR table)
            margin_tiers = []
            only_isolated = asset_meta.get("onlyIsolated", False)
            
            # maxLeverage is the simple max for the first tier
            max_leverage = asset_meta.get("maxLeverage", 1)
            
            # Get IMR (Initial Margin Ratio) brackets
            # Each bracket: [notional_cap, imr]
            imr_brackets = asset_meta.get("imr", [])
            
            if imr_brackets:
                for bracket in imr_brackets:
                    if isinstance(bracket, list) and len(bracket) >= 2:
                        notional_cap = float(bracket[0])  # Max notional for this tier
                        imr = float(bracket[1])  # Initial margin ratio
                        
                        # Convert IMR to leverage: leverage = 1 / IMR
                        tier_max_leverage = int(1 / imr) if imr > 0 else 1
                        
                        margin_tiers.append({
                            "maxNotional": notional_cap,
                            "maxLeverage": tier_max_leverage
                        })
            else:
                # Fallback to simple maxLeverage
                margin_tiers.append({
                    "maxNotional": 1_000_000_000,  # Large default
                    "maxLeverage": max_leverage
                })
            
            leverage_limits[symbol] = {
                "maxLeverage": max_leverage,
                "onlyIsolated": only_isolated,
                "marginTiers": margin_tiers
            }
        
        return leverage_limits
        
    except Exception as e:
        logger.error(f"Failed to fetch leverage limits: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# --- PORTFOLIO VALIDATION (Custom Portfolio Manager) ---

class PortfolioPositionInput(BaseModel):
    asset: str
    side: str  # 'long' or 'short'
    size: float
    leverage: int

class ValidatePositionRequest(BaseModel):
    asset: str
    size: float
    leverage: int
    current_portfolio: List[PortfolioPositionInput]

class SplitPositionRequest(BaseModel):
    asset: str
    target_size: float
    leverage: int
    side: str
    current_portfolio: List[PortfolioPositionInput]

@router.post("/portfolio/validate_position")
async def validate_position(request: ValidatePositionRequest):
    """
    Validate a manual portfolio position against Hyperliquid constraints.
    Educational endpoint - does not execute any real orders.
    """
    try:
        from engine.position_manager import position_manager, PortfolioPosition
        
        # Get live limits
        limits = await position_manager.get_leverage_limits()
        
        # Convert portfolio to internal format
        portfolio = [
            PortfolioPosition(
                asset=p.asset,
                side=p.side,
                size=p.size,
                leverage=p.leverage
            )
            for p in request.current_portfolio
        ]
        
        # Validate
        result = position_manager.validate_position(
            asset=request.asset,
            size=request.size,
            leverage=request.leverage,
            current_portfolio=portfolio,
            limits=limits
        )
        
        return {
            "isValid": result.is_valid,
            "asset": result.asset,
            "requestedSize": result.requested_size,
            "requestedLeverage": result.requested_leverage,
            "adjustedSize": result.adjusted_size,
            "adjustedLeverage": result.adjusted_leverage,
            "exceedsLeverageLimit": result.exceeds_leverage_limit,
            "exceedsSizeLimit": result.exceeds_size_limit,
            "exceedsExposureLimit": result.exceeds_exposure_limit,
            "message": result.message,
            "suggestions": result.suggestions
        }
        
    except Exception as e:
        logger.error(f"Failed to validate position: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/portfolio/suggest_split")
async def suggest_position_split(request: SplitPositionRequest):
    """
    Suggest how to split an oversized position into compliant sub-positions.
    Educational endpoint for the manual portfolio manager.
    """
    try:
        from engine.position_manager import position_manager, PortfolioPosition
        
        # Get live limits
        limits = await position_manager.get_leverage_limits()
        
        # Convert portfolio
        portfolio = [
            PortfolioPosition(
                asset=p.asset,
                side=p.side,
                size=p.size,
                leverage=p.leverage
            )
            for p in request.current_portfolio
        ]
        
        # Get split suggestions
        suggestions = position_manager.suggest_position_split(
            asset=request.asset,
            target_size=request.target_size,
            leverage=request.leverage,
            side=request.side,
            current_portfolio=portfolio,
            limits=limits
        )
        
        return {
            "totalRequested": request.target_size,
            "splitCount": len(suggestions),
            "positions": [
                {
                    "asset": s.asset,
                    "side": s.side,
                    "size": s.size,
                    "leverage": s.leverage,
                    "margin": s.margin,
                    "reason": s.reason
                }
                for s in suggestions
            ]
        }
        
    except Exception as e:
        logger.error(f"Failed to suggest split: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/portfolio/calculate_exposure")
async def calculate_portfolio_exposure(positions: List[PortfolioPositionInput]):
    """
    Calculate total leveraged exposure for a portfolio.
    """
    try:
        from engine.position_manager import position_manager, PortfolioPosition
        
        portfolio = [
            PortfolioPosition(
                asset=p.asset,
                side=p.side,
                size=p.size,
                leverage=p.leverage
            )
            for p in positions
        ]
        
        exposure = position_manager.calculate_portfolio_exposure(portfolio)
        max_exposure = position_manager.max_total_exposure
        
        return {
            "totalExposure": exposure,
            "maxExposure": max_exposure,
            "utilizationPercent": (exposure / max_exposure * 100) if max_exposure > 0 else 0,
            "remainingCapacity": max(0, max_exposure - exposure)
        }
        
    except Exception as e:
        logger.error(f"Failed to calculate exposure: {e}")
        raise HTTPException(status_code=500, detail=str(e))
