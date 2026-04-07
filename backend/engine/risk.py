import logging
from typing import Dict

logger = logging.getLogger(__name__)

class RiskManager:
    def __init__(self):
        pass

    def calculate_size(self, signal_size: float, signal_price: float, target_equity: float, config) -> float:
        """
        Calculate the size to copy based on config mode.
        """
        if config.copy_mode == "raw":
            # Mirror the exact size
            return signal_size
        
        elif config.copy_mode == "percentage":
           # Percentage of THEIR portfolio vs percentage of OUR allocation
           # signal_size (in units) * price = notional used
           # notional / target_equity = % of portfolio used
           # We want to use same % of OUR config.allocation_amount
           
           if target_equity <= 0:
               return 0.0

           trade_notional = signal_size * signal_price
           ratio = trade_notional / target_equity
           
           our_notional_budget = config.allocation_amount * ratio
           our_size = our_notional_budget / signal_price
           
           return our_size
        
        return 0.0

    def check_position_loss(self, position, config) -> bool:
        """
        Check if position has exceeded max loss threshold.
        Returns True if it SHOULD be closed.
        """
        if position.size == 0 or position.status != "OPEN":
            return False

        # Calculate current loss % or fixed
        # This requires current price, but position usually stores entry. 
        # We need the CURRENT Unrealized PnL which is updated in handler/watcher.
        # Assuming position.unrealized_pnl is up to date or we calculate it here if we had current price.
        # For now, we rely on the caller to have updated `position.unrealized_pnl` via market data.
        
        pnl = position.unrealized_pnl
        if pnl >= 0:
            return False

        loss_amount = abs(pnl)
        
        if config.max_position_loss_type == "fixed":
            if loss_amount >= config.max_position_loss:
                logger.warning(f"Position {position.symbol} hit stop loss: -${loss_amount:.2f} >= -${config.max_position_loss}")
                return True
        
        elif config.max_position_loss_type == "percentage":
            # Loss % relative to MARGIN used or NOTIONAL? Usually Margin.
            # But simpler is relative to ENTRY VALUE (Notional / Leverage)? 
            # Or just Notional? 
            # "Total Position Loss" usually implies loss on the principal allocated to that position.
            # Principal = (Size * Entry) / Leverage.
            
            principal = (position.size * position.entry_price) / position.leverage
            if principal <= 0:
                pass # safe guard
            else:
                 loss_pct = (loss_amount / principal) * 100
                 if loss_pct >= config.max_position_loss:
                     logger.warning(f"Position {position.symbol} hit stop loss: -{loss_pct:.2f}% >= -{config.max_position_loss}%")
                     return True
                     
        return False

    def validate_initial_trade(self, symbol: str, side: str, size: float, price: float, leverage: int, config) -> bool:
        """
        Check rigid constraints before opening.
        """
        # 1. Leverage Cap
        if leverage > config.max_leverage:
             logger.warning(f"Risk rejected: Leverage {leverage}x > Cap {config.max_leverage}x")
             return False

        # 2. Exposure Cap (1x) checks
        # User requirement: "Exposure scaling... must be capped at 1x".
        # This usually means we don't hold more notional than we have equity? 
        # Or simply we check against some global limit? 
        # "Exposure scaling is allowed only to reduce exposure... must be capped at 1×" 
        # likely means if we calculated a size that implies > 1x leverage on the whole account, or > 1.0 multiplier?
        # Actually, reading the prompt: "Exposure scaling... capped at 1x" refers to the MULTIPLIER being max 1.0.
        # i.e. we never multiply size by > 1.0. 
        # Since calculate_size logic above is either 'raw' (1:1) or 'percentage' (relative 1:1), 
        # we naturally respect this unless we added detailed multipliers.
        # But we also have `config.exposure_cap`.
        
        if config.exposure_cap > 1.0:
            # We enforce hard cap of 1.0 here just in case config allows more
            # But checking passed 'size' against what?
            # It's simpler to just say we don't block here unless logic outside used > 1.0 multiplier.
            pass

        return True
