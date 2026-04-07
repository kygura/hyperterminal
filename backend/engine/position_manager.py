"""
Position Manager for Hyperliquid Manual Portfolio

Validates simulated positions against live Hyperliquid constraints.
Provides educational feedback on leverage limits, position sizing, and exposure management.
This is for the custom portfolio manager (Charts page) - NO REAL ORDER EXECUTION.
"""

from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime, timedelta
import asyncio

@dataclass
class LeverageLimit:
    """Leverage limit tier for an asset"""
    max_notional: float  # Maximum position size in USD at this tier
    max_leverage: int    # Maximum leverage allowed at this tier

@dataclass
class AssetLimits:
    """Complete leverage limits for an asset"""
    asset: str
    max_leverage: int  # Absolute maximum leverage
    only_isolated: bool  # If true, must use isolated margin
    margin_tiers: List[LeverageLimit]  # Tiered limits

@dataclass
class ValidationResult:
    """Result of position validation"""
    is_valid: bool
    asset: str
    requested_size: float
    requested_leverage: int
    
    # Adjusted values (if needed)
    adjusted_size: float
    adjusted_leverage: int
    
    # Validation details
    exceeds_leverage_limit: bool
    exceeds_size_limit: bool
    exceeds_exposure_limit: bool
    
    # Helpful messages
    message: str
    suggestions: List[str]
    
@dataclass
class SuggestedPosition:
    """Suggested position when splitting is needed"""
    asset: str
    side: str
    size: float
    leverage: int
    margin: float
    reason: str

@dataclass
class PortfolioPosition:
    """Simplified position for exposure calculation"""
    asset: str
    side: str  # 'long' or 'short'
    size: float  # Notional size in USD
    leverage: int


class PositionManager:
    """
    Validates manual portfolio positions against Hyperliquid's real-world constraints.
    Educational tool to help users understand realistic position sizing.
    """
    
    def __init__(self):
        self._limits_cache: Optional[Dict[str, AssetLimits]] = None
        self._cache_timestamp: Optional[datetime] = None
        self._cache_ttl = timedelta(minutes=5)
        
        # Configurable exposure limit (can be updated based on actual HL limits)
        self.max_total_exposure = 2_000_000  # $2M default
    
    async def get_leverage_limits(self, force_refresh: bool = False) -> Dict[str, AssetLimits]:
        """
        Fetch leverage limits from Hyperliquid metadata API with caching.
        
        Args:
            force_refresh: Force cache refresh
            
        Returns:
            Dictionary mapping asset symbol to AssetLimits
        """
        # Check cache
        if not force_refresh and self._limits_cache and self._cache_timestamp:
            if datetime.utcnow() - self._cache_timestamp < self._cache_ttl:
                return self._limits_cache
        
        # Fetch fresh limits
        from data.hl_client.client import HyperliquidClient
        client = HyperliquidClient()
        
        try:
            meta, _ = await client.get_meta_and_asset_ctxs()
            
            if not meta:
                raise Exception("Failed to fetch metadata from Hyperliquid")
            
            universe = meta.get("universe", [])
            limits_dict = {}
            
            for asset_meta in universe:
                symbol = asset_meta.get("name")
                if not symbol:
                    continue
                
                max_leverage = asset_meta.get("maxLeverage", 1)
                only_isolated = asset_meta.get("onlyIsolated", False)
                
                # Parse margin tiers
                margin_tiers = []
                imr_brackets = asset_meta.get("imr", [])
                
                if imr_brackets:
                    for bracket in imr_brackets:
                        if isinstance(bracket, list) and len(bracket) >= 2:
                            notional_cap = float(bracket[0])
                            imr = float(bracket[1])
                            tier_leverage = int(1 / imr) if imr > 0 else 1
                            
                            margin_tiers.append(LeverageLimit(
                                max_notional=notional_cap,
                                max_leverage=tier_leverage
                            ))
                else:
                    # Fallback: single tier with max leverage
                    margin_tiers.append(LeverageLimit(
                        max_notional=1_000_000_000,  # Very large
                        max_leverage=max_leverage
                    ))
                
                limits_dict[symbol] = AssetLimits(
                    asset=symbol,
                    max_leverage=max_leverage,
                    only_isolated=only_isolated,
                    margin_tiers=margin_tiers
                )
            
            # Update cache
            self._limits_cache = limits_dict
            self._cache_timestamp = datetime.utcnow()
            
            return limits_dict
            
        except Exception as e:
            # If fetch fails and we have cache, return stale cache
            if self._limits_cache:
                print(f"[WARN] Using stale limits cache due to error: {e}")
                return self._limits_cache
            raise
    
    def calculate_portfolio_exposure(self, positions: List[PortfolioPosition]) -> float:
        """
        Calculate total leveraged exposure for a portfolio.
        
        Exposure = sum of (position_size for all positions)
        This represents total notional value across all positions.
        
        Args:
            positions: List of portfolio positions
            
        Returns:
            Total exposure in USD
        """
        total = 0.0
        for pos in positions:
            total += abs(pos.size)  # Notional size
        return total
    
    def _find_applicable_tier(
        self, 
        asset_limits: AssetLimits, 
        size: float, 
        leverage: int
    ) -> Optional[LeverageLimit]:
        """Find the margin tier that applies to this position."""
        for tier in asset_limits.margin_tiers:
            if size <= tier.max_notional and leverage <= tier.max_leverage:
                return tier
        return None
    
    def validate_position(
        self,
        asset: str,
        size: float,
        leverage: int,
        current_portfolio: List[PortfolioPosition],
        limits: Dict[str, AssetLimits]
    ) -> ValidationResult:
        """
        Validate a position against Hyperliquid constraints.
        
        Args:
            asset: Asset symbol (e.g., "BTC", "ETH")
            size: Position size in USD
            leverage: Requested leverage
            current_portfolio: Existing positions in portfolio
            limits: Live leverage limits from Hyperliquid
            
        Returns:
            ValidationResult with details and suggestions
        """
        # Get asset limits
        asset_limits = limits.get(asset)
        if not asset_limits:
            # Unknown asset - use conservative defaults
            return ValidationResult(
                is_valid=False,
                asset=asset,
                requested_size=size,
                requested_leverage=leverage,
                adjusted_size=size,
                adjusted_leverage=1,
                exceeds_leverage_limit=True,
                exceeds_size_limit=False,
                exceeds_exposure_limit=False,
                message=f"Unknown asset '{asset}'. Using conservative 1x leverage.",
                suggestions=["Verify asset symbol", "Use known assets like BTC, ETH, SOL"]
            )
        
        # Calculate current exposure
        current_exposure = self.calculate_portfolio_exposure(current_portfolio)
        new_exposure = current_exposure + size
        
        # Check constraints
        exceeds_leverage = leverage > asset_limits.max_leverage
        exceeds_exposure = new_exposure > self.max_total_exposure
        
        # Find applicable tier
        tier = self._find_applicable_tier(asset_limits, size, leverage)
        exceeds_size = tier is None
        
        # Determine adjusted values
        adjusted_size = size
        adjusted_leverage = leverage
        suggestions = []
        
        if exceeds_leverage:
            adjusted_leverage = asset_limits.max_leverage
            suggestions.append(f"Reduce leverage to {asset_limits.max_leverage}x (asset maximum)")
        
        if exceeds_size:
            # Find maximum size at requested leverage
            max_size = 0
            for t in asset_limits.margin_tiers:
                if leverage <= t.max_leverage:
                    max_size = t.max_notional
                    break
            
            if max_size > 0:
                adjusted_size = max_size
                suggestions.append(
                    f"Reduce size to ${max_size:,.0f} (max at {leverage}x) or reduce leverage"
                )
            else:
                suggestions.append(
                    f"Position too large for {leverage}x leverage on {asset}"
                )
        
        if exceeds_exposure:
            available_exposure = self.max_total_exposure - current_exposure
            suggestions.append(
                f"Total portfolio exposure would be ${new_exposure:,.0f}, "
                f"exceeding ${self.max_total_exposure:,.0f} limit. "
                f"Available: ${available_exposure:,.0f}"
            )
            adjusted_size = min(adjusted_size, available_exposure)
        
        # Build message
        is_valid = not (exceeds_leverage or exceeds_size or exceeds_exposure)
        
        if is_valid:
            message = f"✅ Position is valid and compliant with Hyperliquid limits"
        else:
            issues = []
            if exceeds_leverage:
                issues.append(f"leverage {leverage}x > max {asset_limits.max_leverage}x")
            if exceeds_size:
                issues.append(f"size ${size:,.0f} exceeds tier limits")
            if exceeds_exposure:
                issues.append(f"total exposure ${new_exposure:,.0f} > ${self.max_total_exposure:,.0f}")
            message = f"⚠️ Position exceeds limits: {', '.join(issues)}"
        
        return ValidationResult(
            is_valid=is_valid,
            asset=asset,
            requested_size=size,
            requested_leverage=leverage,
            adjusted_size=adjusted_size,
            adjusted_leverage=adjusted_leverage,
            exceeds_leverage_limit=exceeds_leverage,
            exceeds_size_limit=exceeds_size,
            exceeds_exposure_limit=exceeds_exposure,
            message=message,
            suggestions=suggestions
        )
    
    def suggest_position_split(
        self,
        asset: str,
        target_size: float,
        leverage: int,
        side: str,
        current_portfolio: List[PortfolioPosition],
        limits: Dict[str, AssetLimits]
    ) -> List[SuggestedPosition]:
        """
        Suggest how to split an oversized position into compliant sub-positions.
        
        Args:
            asset: Asset symbol
            target_size: Desired total position size
            leverage: Desired leverage
            side: 'long' or 'short'
            current_portfolio: Existing positions
            limits: Live leverage limits
            
        Returns:
            List of suggested sub-positions that sum to target (or as close as possible)
        """
        asset_limits = limits.get(asset)
        if not asset_limits:
            return []
        
        current_exposure = self.calculate_portfolio_exposure(current_portfolio)
        remaining_exposure_budget = max(0, self.max_total_exposure - current_exposure)
        
        positions = []
        remaining_size = min(target_size, remaining_exposure_budget)
        
        # Sort tiers by leverage (descending) to use highest leverage tiers first
        sorted_tiers = sorted(
            asset_limits.margin_tiers,
            key=lambda t: t.max_leverage,
            reverse=True
        )
        
        count = 1
        while remaining_size > 0 and len(positions) < 10:  # Max 10 splits for sanity
            # Find best tier for this slice
            best_tier = None
            for tier in sorted_tiers:
                if leverage <= tier.max_leverage:
                    best_tier = tier
                    break
            
            if not best_tier:
                # Can't fit any more with requested leverage
                positions.append(SuggestedPosition(
                    asset=asset,
                    side=side,
                    size=0,
                    leverage=leverage,
                    margin=0,
                    reason=f"Cannot accommodate remaining ${remaining_size:,.0f} at {leverage}x leverage"
                ))
                break
            
            # Size for this position
            pos_size = min(remaining_size, best_tier.max_notional)
            pos_leverage = min(leverage, best_tier.max_leverage)
            pos_margin = pos_size / pos_leverage
            
            positions.append(SuggestedPosition(
                asset=asset,
                side=side,
                size=pos_size,
                leverage=pos_leverage,
                margin=pos_margin,
                reason=f"Split {count}: ${pos_size:,.0f} @ {pos_leverage}x (tier limit: ${best_tier.max_notional:,.0f})"
            ))
            
            remaining_size -= pos_size
            count += 1
        
        return positions


# Global instance
position_manager = PositionManager()
