import logging
import os
from typing import Optional, Dict, Any
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


class Executor:
    """
    Trade execution engine using the official Hyperliquid Python SDK.
    
    Executes trades on the vault wallet using the agent wallet's private key for signing.
    The agent wallet must have been authorized by the vault wallet via the Hyperliquid dashboard.
    """
    
    DEFAULT_SLIPPAGE = 0.01  # 1% slippage for market orders
    
    def __init__(self, agent_private_key: str = None, vault_address: str = None):
        from core.settings import settings
        
        self.agent_private_key = agent_private_key or settings.hyperliquid.agent_private_key
        self.vault_address = vault_address or settings.hyperliquid.vault_address
        self.api_url = settings.hyperliquid.api_url
        
        self.exchange = None
        self.info = None
        self.account = None
        
        if not self.agent_private_key:
            logger.warning("No agent private key found. Execution will be in READ-ONLY/MOCK mode.")
        else:
            self._initialize_sdk()
    
    def _initialize_sdk(self):
        """Initialize the Hyperliquid SDK Exchange and Info clients."""
        try:
            import eth_account
            from hyperliquid.exchange import Exchange
            from hyperliquid.info import Info
            
            # Create account from agent private key
            self.account = eth_account.Account.from_key(self.agent_private_key)
            
            # Initialize Info client for read operations
            self.info = Info(self.api_url, skip_ws=True)
            
            # Initialize Exchange client for trade execution
            # wallet: agent wallet for signing trades
            # vault_address: trades execute on behalf of this vault
            self.exchange = Exchange(
                wallet=self.account,
                base_url=self.api_url,
                vault_address=self.vault_address
            )
            
            logger.info(f"Executor initialized with agent {self.account.address} for vault {self.vault_address}")
            
        except Exception as e:
            logger.error(f"Failed to initialize SDK: {e}")
            self.exchange = None
            self.info = None

    async def execute_order(
        self, 
        symbol: str, 
        is_buy: bool, 
        size: float, 
        price: float, 
        order_type: str = "LIMIT",
        reduce_only: bool = False,
        slippage: float = None
    ) -> Dict[str, Any]:
        """
        Execute a trade order on Hyperliquid.
        
        Args:
            symbol: Trading pair (e.g., "ETH", "BTC")
            is_buy: True for buy/long, False for sell/short
            size: Order size in base asset units
            price: Limit price (used for slippage calc in market orders)
            order_type: "MARKET" or "LIMIT"
            reduce_only: If True, order can only reduce position
            slippage: Slippage tolerance for market orders (default 1%)
            
        Returns:
            Order result dictionary from the SDK
        """

        try:
            slippage = slippage or self.DEFAULT_SLIPPAGE
            
            if order_type.upper() == "MARKET":
                # Market order using aggressive IoC limit
                result = self.exchange.market_open(
                    name=symbol,
                    is_buy=is_buy,
                    sz=size,
                    px=price, # Reference price for slippage calculation
                    slippage=slippage
                )
            elif order_type.upper() == "LIMIT":
                # Limit order with Good-Till-Cancelled
                result = self.exchange.order(
                    name=symbol,
                    is_buy=is_buy,
                    sz=size,
                    limit_px=price,
                    order_type={"limit": {"tif": "Gtc"}},
                    reduce_only=reduce_only
                )
            
            # Log the result
            if result.get("status") == "ok":
                statuses = result.get("response", {}).get("data", {}).get("statuses", [])
                for status in statuses:
                    if "filled" in status:
                        filled = status["filled"]
                        logger.info(f"Order filled: {symbol} {filled.get('totalSz')} @ {filled.get('avgPx')}")
                    elif "resting" in status:
                        resting = status["resting"]
                        logger.info(f"Order resting: {symbol} oid={resting.get('oid')}")
                    elif "error" in status:
                        logger.error(f"Order error: {status['error']}")
            else:
                logger.error(f"Order failed: {result}")
                
            return result
            
        except Exception as e:
            logger.error(f"Execution failed for {symbol}: {e}")
            return {"status": "error", "error": str(e)}

    async def close_position(
        self, 
        symbol: str, 
        size: Optional[float] = None,
        slippage: float = None
    ) -> Dict[str, Any]:
        """
        Close an open position using a market order.
        
        Args:
            symbol: Trading pair to close
            size: Amount to close (None = close entire position)
            slippage: Slippage tolerance (default 1%)
            
        Returns:
            Order result dictionary
        """
        try:
            slippage = slippage or self.DEFAULT_SLIPPAGE
            
            result = self.exchange.market_close(
                coin=symbol,
                sz=size,  # None closes entire position
                slippage=slippage
            )
            
            if result.get("status") == "ok":
                statuses = result.get("response", {}).get("data", {}).get("statuses", [])
                for status in statuses:
                    if "filled" in status:
                        filled = status["filled"]
                        logger.info(f"Position closed: {symbol} {filled.get('totalSz')} @ {filled.get('avgPx')}")
                    elif "error" in status:
                        logger.error(f"Close error: {status['error']}")
            else:
                logger.error(f"Close failed: {result}")
                
            return result
            
        except Exception as e:
            logger.error(f"Failed to close position for {symbol}: {e}")
            return {"status": "error", "error": str(e)}

    async def cancel_order(self, symbol: str, order_id: int) -> Dict[str, Any]:
        """
        Cancel an open order.
        
        Args:
            symbol: Trading pair
            order_id: Order ID to cancel
            
        Returns:
            Cancel result dictionary
        """

        try:
            result = self.exchange.cancel(name=symbol, oid=order_id)
            
            if result.get("status") == "ok":
                logger.info(f"Order cancelled: {symbol} oid={order_id}")
            else:
                logger.error(f"Cancel failed: {result}")
                
            return result
            
        except Exception as e:
            logger.error(f"Failed to cancel order {order_id} for {symbol}: {e}")
            return {"status": "error", "error": str(e)}

    async def get_open_orders(self, address: str = None) -> list:
        """
        Fetch open orders for a specific user address.
        
        Args:
            address: Wallet address (defaults to vault address)
            
        Returns:
            List of open orders
        """
        address = address or self.vault_address
        
        try:
            return self.info.open_orders(address)
        except Exception as e:
            logger.error(f"Failed to fetch open orders for {address}: {e}")
            return []

    async def get_user_state(self, address: str = None) -> Optional[Dict[str, Any]]:
        """
        Get the current user state including positions and margin.
        
        Args:
            address: Wallet address (defaults to vault address)
            
        Returns:
            User state dictionary or None
        """
        address = address or self.vault_address
        
        try:
            return self.info.user_state(address)
        except Exception as e:
            logger.error(f"Failed to fetch user state for {address}: {e}")
            return None
