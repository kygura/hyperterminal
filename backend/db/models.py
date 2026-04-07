from typing import Optional, List
from datetime import datetime
from sqlmodel import Field, SQLModel, Relationship

class Wallet(SQLModel, table=True):
    address: str = Field(primary_key=True)
    name: Optional[str] = None
    is_active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    
    positions: List["Position"] = Relationship(back_populates="wallet", sa_relationship_kwargs={"cascade": "all, delete-orphan"})
    trades: List["Trade"] = Relationship(back_populates="wallet", sa_relationship_kwargs={"cascade": "all, delete-orphan"})
    snapshots: List["WalletSnapshot"] = Relationship(back_populates="wallet", sa_relationship_kwargs={"cascade": "all, delete-orphan"})
    copy_config: Optional["CopyConfig"] = Relationship(back_populates="wallet", sa_relationship_kwargs={"cascade": "all, delete-orphan"})
    pending_trades: List["PendingTrade"] = Relationship(back_populates="wallet", sa_relationship_kwargs={"cascade": "all, delete-orphan"})

class Goal(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    target_pnl: float
    current_pnl: float = Field(default=0.0)
    start_date: datetime = Field(default_factory=datetime.utcnow)
    end_date: Optional[datetime] = None
    status: str = Field(default="ACTIVE") # ACTIVE, ACHIEVED, FAILED

class Position(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    wallet_address: str = Field(foreign_key="wallet.address")
    symbol: str
    side: str  # LONG, SHORT
    size: float
    entry_price: float
    leverage: int
    unrealized_pnl: float = 0.0
    status: str = Field(default="OPEN")  # OPEN, CLOSED
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    wallet: Wallet = Relationship(back_populates="positions")

class Trade(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    wallet_address: str = Field(foreign_key="wallet.address")
    symbol: str
    side: str
    size: float
    price: float
    realized_pnl: float = Field(default=0.0)
    fee: float = Field(default=0.0)
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    type: str = "MARKET" # MARKET, LIMIT
    
    wallet: Wallet = Relationship(back_populates="trades")

class WalletSnapshot(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    wallet_address: str = Field(foreign_key="wallet.address")
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    total_equity: float
    
    wallet: Wallet = Relationship(back_populates="snapshots")


class CopyConfig(SQLModel, table=True):
    wallet_address: str = Field(foreign_key="wallet.address", primary_key=True)
    mode: str = Field(default="manual") # auto, manual
    is_paused: bool = Field(default=False)
    copy_mode: str = Field(default="percentage") # raw, percentage
    allocation_amount: float = Field(default=1000.0)
    max_position_loss: float = Field(default=10.0)
    max_position_loss_type: str = Field(default="percentage") # fixed, percentage
    exposure_cap: float = Field(default=1.0)
    max_leverage: int = Field(default=10)
    # Removing legacy fields like daily_loss_limit from here as they are deprecated
    
    wallet: Wallet = Relationship(back_populates="copy_config")

class PendingTrade(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    wallet_address: str = Field(foreign_key="wallet.address")
    symbol: str
    side: str
    size: float
    leverage: int
    price: float # Entry price at time of signal
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    status: str = Field(default="PENDING") # PENDING, APPROVED, REJECTED, EXPIRED
    
    wallet: Wallet = Relationship(back_populates="pending_trades")

class UserSettings(SQLModel, table=True):
    """Store user preferences and settings"""
    id: Optional[int] = Field(default=None, primary_key=True)
    setting_key: str = Field(index=True, unique=True)
    setting_value: str  # JSON string
    updated_at: datetime = Field(default_factory=datetime.utcnow)

class AllocatedPosition(SQLModel, table=True):
    """Store allocated positions from capital allocation simulator"""
    __tablename__ = "allocated_positions"
    
    id: str = Field(primary_key=True)  # UUID from frontend
    asset: str
    side: str  # 'long' or 'short'
    size: float  # Position size in USD
    leverage: int
    margin: float  # Required margin
    entry_price: float
    entry_timestamp: int  # Milliseconds since epoch
    exit_price: Optional[float] = None
    exit_timestamp: Optional[int] = None
    status: str = Field(default="open")  # 'open' or 'closed'
    adjustment_reason: Optional[str] = None  # If resized, why
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
