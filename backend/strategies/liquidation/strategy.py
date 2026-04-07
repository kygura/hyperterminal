import asyncio
import logging
from typing import Dict, List, Optional
from dataclasses import asdict

from ...hl_client.client import HyperliquidClient
from ...hl_client.websocket import HyperliquidWebSocket
from ...engine.executor import Executor as TradeExecutor # The existing engine executor

from .models import Regime, TradeSignal, RiskConfig
from .data_collector import DataCollector
from .wallet_tracker import WalletTracker
from .cluster_manager import ClusterManager
from .lvs_calculator import LVSCalculator
from .regime_classifier import RegimeClassifier
from .trading_logic import TradingLogic
from .position_sizer import PositionSizer
from .risk_manager import RiskManager
from .executor import MakerFirstExecutor, ExecutionMode

logger = logging.getLogger(__name__)

class LiquidationStrategy:
    def __init__(self, 
                 info_client: HyperliquidClient, 
                 trade_executor: TradeExecutor,
                 ws_client: HyperliquidWebSocket,
                 config: Dict):
        
        self.config = config
        self.risk_config = RiskConfig(**config.get("risk", {}))
        
        # Components
        self.data_collector = DataCollector(info_client, ws_client)
        self.wallet_tracker = WalletTracker(info_client)
        self.cluster_manager = ClusterManager(config)
        
        self.lvs_calc = LVSCalculator(config)
        self.regime_classifier = RegimeClassifier(config)
        self.trading_logic = TradingLogic(config)
        self.position_sizer = PositionSizer(config)
        self.risk_manager = RiskManager(config)
        self.executor = MakerFirstExecutor(trade_executor, config)
        
        self.symbols = config.get("symbols", ["BTC", "ETH", "SOL"])
        self.running = False

    async def start(self):
        logger.info("Starting Liquidation Strategy...")
        self.running = True
        
        # Start Data Streams
        await self.data_collector.start(self.symbols)
        await self.wallet_tracker.initialize()
        
        # Start Loop
        asyncio.create_task(self._strategy_loop())
        asyncio.create_task(self._background_tasks())

    async def stop(self):
        self.running = False
        logger.info("Stopping Strategy...")

    async def _strategy_loop(self):
        while self.running:
            try:
                for symbol in self.symbols:
                    await self._process_symbol(symbol)
                
                await asyncio.sleep(1) # 1s tick
            except Exception as e:
                logger.error(f"Strategy Loop Error: {e}", exc_info=True)
                await asyncio.sleep(5)

    async def _process_symbol(self, symbol: str):
        # 1. Update State
        market_data = self.data_collector.get_market_data(symbol)
        
        # 2. Update Clusters
        explicit_liqs = self.wallet_tracker.get_liquidation_prices(symbol)
        self.cluster_manager.update_explicit(symbol, explicit_liqs)
        
        inferred_profile = market_data.get("oi_profile", []) # [(price, oi, bias)]
        self.cluster_manager.update_inferred(symbol, inferred_profile)
        
        clusters = self.cluster_manager.get_clusters(symbol)
        
        # 3. Compute Signals
        lvs = self.lvs_calc.calculate(symbol, market_data, clusters)
        market_data["lvs"] = lvs # Inject LVS
        
        regime = self.regime_classifier.classify(lvs, market_data)
        
        # 4. Generate Signal
        current_pos = None # Need to fetch from executor?
        # current_pos = self.trade_executor.get_position(symbol)
        
        signal = self.trading_logic.check_signal(symbol, regime, market_data, clusters, current_pos)
        
        if signal:
            await self._execute_signal(signal, current_pos)
            
        # 5. Check Exits (if held)
        if current_pos:
            should_exit = self.trading_logic.check_exit(symbol, current_pos, regime, market_data)
            if should_exit:
                await self.executor.client.close_position(symbol) # Close

    async def _execute_signal(self, signal: TradeSignal, current_pos: Optional[Dict]):
        # Check Risk
        portfolio_equity = 10000.0 # Mock or fetch
        # portfolio_equity = self.executor.client.get_user_state().balance
        
        size = self.position_sizer.calculate_size(signal, portfolio_equity, self.risk_config)
        
        if size <= 0:
            return

        # Check Stops (calculation only, executor handles orders)
        stop_price = self.risk_manager.get_stop_price(
            signal.price, signal.direction, signal.regime, signal.cluster, self.risk_config
        )
        
        # Determine Mode
        mode = ExecutionMode.AGGRESSIVE if signal.regime == Regime.REGIME_A else ExecutionMode.MAKER_ONLY
        
        # Execute
        logger.info(f"Signal: {signal.regime} {signal.direction} {signal.symbol} Size: {size}")
        await self.executor.execute(signal, size, mode)

    async def _background_tasks(self):
        while self.running:
            # Poll Wallet Tracker
            await self.wallet_tracker.poll_liquidation_prices()
            
            # Decay Clusters
            self.cluster_manager.decay_and_prune()
            
            await asyncio.sleep(60)
