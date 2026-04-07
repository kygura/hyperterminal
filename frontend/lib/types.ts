export interface Signal {
  id: string;
  timestamp: string;
  asset: string;
  direction: "Long" | "Short";
  conviction: "HIGH" | "MEDIUM" | "LOW";
  regime: string;
  signalCount: number;
  signals: string[];
  price: number | null;
  vwap: number | null;
  timeframe: string;
}

export interface Position {
  id: string;
  asset: string;
  direction: "Long" | "Short";
  mode: "Cross" | "Isolated";
  leverage: number;
  margin: number;
  entryDate: string;
  entryPrice: number;
  exitDate?: string;
  exitPrice?: number;
}

export interface Branch {
  id: string;
  name: string;
  color: string;
  isMain: boolean;
  parentId?: string;
  forkDate: string;
  balance: number;
  sourceType?: string;
  sourcePath?: string;
  updatedAt?: string;
  positions: Position[];
}

export interface BranchMetrics {
  branch: Branch;
  eq: number[];
  forkIdx: number;
  ret: number;
  mdd: number;
  sharpe: number;
  val: number;
}

export interface AccountState {
  crossEquity: number;
  totalEquity: number;
  availableMargin: number;
  crossMarginUsed: number;
  isoMarginUsed: number;
  crossUPnL: number;
  isoUPnL: number;
  crossMM: number;
  crossLiquidated: boolean;
  maxWithdraw: number;
  isoLosses: number;
}

export interface PositionState {
  isActive: boolean;
  isClosed: boolean;
  mark: number;
  entry: number;
  notional: number;
  pnlPct: number;
  unrealizedPnl: number;
  posEquity: number;
  maintenanceMargin: number;
  isLiquidated: boolean;
  entryIdx: number;
  exitIdx: number | null;
}

export interface PortfolioImport {
  portfolio: {
    name: string;
    balance: number;
    positions: Array<{
      asset: string;
      direction: "Long" | "Short";
      mode?: "Cross" | "Isolated";
      leverage: number;
      margin: number;
      entry_date: string;
      entry_price: number;
      exit_date?: string;
      exit_price?: number;
    }>;
  };
}

export interface AssetConfig {
  name: string;
  color: string;
  maxLeverage: number;
}

export const ASSETS: Record<string, AssetConfig> = {
  BTC: { name: "BTC-USD", color: "#f7931a", maxLeverage: 50 },
  ETH: { name: "ETH-USD", color: "#627eea", maxLeverage: 25 },
  SOL: { name: "SOL-USD", color: "#9945ff", maxLeverage: 20 },
  HYPE: { name: "HYPE-USD", color: "#50d2c1", maxLeverage: 20 },
};
