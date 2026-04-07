import type {
  AccountState,
  Branch,
  BranchMetrics,
  Position,
  PositionState,
} from "@/lib/types";
import {
  OHLC_DATES,
  candleAt,
  candlesForAsset,
  dateAt,
  indexAtOrAfter,
  resolveIndex,
} from "@/lib/price-data";

type DateInput = number | string | Date;

export interface DerivedPositionState extends PositionState {
  qty: number;
  liqPrice: number;
  displayPnl: number;
  realizedPnl: number;
  currentDate: string | null;
}

const clamp = (value: number, min: number, max: number): number =>
  Math.min(max, Math.max(min, value));

const signFor = (direction: Position["direction"]): number =>
  direction === "Long" ? 1 : -1;

const qtyFor = (position: Position): number =>
  position.entryPrice > 0
    ? (position.margin * position.leverage) / position.entryPrice
    : 0;

const notionalFor = (position: Position): number => position.margin * position.leverage;

const pnlAtPrice = (position: Position, price: number): number =>
  qtyFor(position) * (price - position.entryPrice) * signFor(position.direction);

const pctFromPnl = (margin: number, pnl: number): number =>
  margin > 0 ? (pnl / margin) * 100 : 0;

const liqPriceFor = (position: Position): number => {
  const mmRate = maintenanceMarginRate(position.leverage);
  const move = (1 - mmRate) / Math.max(position.leverage, 1);
  const basis =
    position.direction === "Long"
      ? position.entryPrice * (1 - move)
      : position.entryPrice * (1 + move);

  return Math.max(0, basis);
};

const candleBreachesLiquidation = (
  position: Position,
  liqPrice: number,
  index: number,
): boolean => {
  const candle = candleAt(position.asset, index);

  if (!candle) {
    return false;
  }

  return position.direction === "Long"
    ? candle.low <= liqPrice
    : candle.high >= liqPrice;
};

const branchForkIndex = (branch: Branch): number => indexAtOrAfter(branch.forkDate);

const segmentValues = (eq: number[], forkIdx = 0): number[] =>
  eq.slice(forkIdx).filter((value) => Number.isFinite(value) && value > 0);

export const maintenanceMarginRate = (leverage: number): number =>
  clamp(0.5 / Math.max(leverage, 1), 0.005, 0.1);

export const posStateAt = (
  position: Position,
  at: DateInput,
): DerivedPositionState => {
  const series = candlesForAsset(position.asset);
  const currentIdx = resolveIndex(at, "before");
  const entryIdx = indexAtOrAfter(position.entryDate);
  const exitIdx = position.exitDate ? indexAtOrAfter(position.exitDate) : null;
  const liqPrice = liqPriceFor(position);
  const notional = notionalFor(position);
  const maintenanceMargin = notional * maintenanceMarginRate(position.leverage);
  const qty = qtyFor(position);

  if (series.length === 0 || currentIdx < entryIdx) {
    return {
      isActive: false,
      isClosed: false,
      mark: position.entryPrice,
      entry: position.entryPrice,
      notional,
      pnlPct: 0,
      unrealizedPnl: 0,
      posEquity: position.margin,
      maintenanceMargin,
      isLiquidated: false,
      entryIdx,
      exitIdx,
      qty,
      liqPrice,
      displayPnl: 0,
      realizedPnl: 0,
      currentDate: dateAt(currentIdx) || null,
    };
  }

  let liquidatedIdx: number | null = null;

  for (let index = entryIdx; index <= currentIdx; index += 1) {
    if (exitIdx !== null && index >= exitIdx) {
      break;
    }

    if (candleBreachesLiquidation(position, liqPrice, index)) {
      liquidatedIdx = index;
      break;
    }
  }

  if (liquidatedIdx !== null) {
    const realizedPnl = pnlAtPrice(position, liqPrice);
    return {
      isActive: false,
      isClosed: false,
      mark: liqPrice,
      entry: position.entryPrice,
      notional,
      pnlPct: pctFromPnl(position.margin, realizedPnl),
      unrealizedPnl: 0,
      posEquity: 0,
      maintenanceMargin,
      isLiquidated: true,
      entryIdx,
      exitIdx,
      qty,
      liqPrice,
      displayPnl: realizedPnl,
      realizedPnl,
      currentDate: dateAt(liquidatedIdx),
    };
  }

  if (exitIdx !== null && currentIdx >= exitIdx) {
    const exitPrice = position.exitPrice ?? series[exitIdx]?.close ?? position.entryPrice;
    const realizedPnl = pnlAtPrice(position, exitPrice);

    return {
      isActive: false,
      isClosed: true,
      mark: exitPrice,
      entry: position.entryPrice,
      notional,
      pnlPct: pctFromPnl(position.margin, realizedPnl),
      unrealizedPnl: 0,
      posEquity: 0,
      maintenanceMargin,
      isLiquidated: false,
      entryIdx,
      exitIdx,
      qty,
      liqPrice,
      displayPnl: realizedPnl,
      realizedPnl,
      currentDate: dateAt(exitIdx),
    };
  }

  const mark = series[currentIdx]?.close ?? position.entryPrice;
  const unrealizedPnl = pnlAtPrice(position, mark);
  const posEquity = position.margin + unrealizedPnl;

  return {
    isActive: true,
    isClosed: false,
    mark,
    entry: position.entryPrice,
    notional,
    pnlPct: pctFromPnl(position.margin, unrealizedPnl),
    unrealizedPnl,
    posEquity,
    maintenanceMargin,
    isLiquidated: false,
    entryIdx,
    exitIdx,
    qty,
    liqPrice,
    displayPnl: unrealizedPnl,
    realizedPnl: 0,
    currentDate: dateAt(currentIdx) || null,
  };
};

export const accountStateAt = (
  branch: Branch,
  at: DateInput,
): AccountState => {
  const currentIdx = resolveIndex(at, "before");
  const forkIdx = branchForkIndex(branch);

  if (currentIdx < forkIdx) {
    return {
      crossEquity: branch.balance,
      totalEquity: branch.balance,
      availableMargin: branch.balance,
      crossMarginUsed: 0,
      isoMarginUsed: 0,
      crossUPnL: 0,
      isoUPnL: 0,
      crossMM: 0,
      crossLiquidated: false,
      maxWithdraw: branch.balance,
      isoLosses: 0,
    };
  }

  let cash = branch.balance;
  let crossMarginUsed = 0;
  let isoMarginUsed = 0;
  let crossUPnL = 0;
  let isoUPnL = 0;
  let activeIsoEquity = 0;
  let crossMM = 0;
  let isoLosses = 0;
  let hasActiveCross = false;

  for (const position of branch.positions) {
    const state = posStateAt(position, currentIdx);

    if (state.entryIdx > currentIdx) {
      continue;
    }

    if (state.isActive) {
      if (position.mode === "Cross") {
        hasActiveCross = true;
        crossMarginUsed += position.margin;
        crossUPnL += state.unrealizedPnl;
        crossMM += state.maintenanceMargin;
      } else {
        cash -= position.margin;
        isoMarginUsed += position.margin;
        isoUPnL += state.unrealizedPnl;
        activeIsoEquity += state.posEquity;
      }
      continue;
    }

    cash += state.realizedPnl;

    if (position.mode === "Isolated" && state.isLiquidated) {
      isoLosses += Math.abs(state.realizedPnl);
    }
  }

  const crossEquity = cash + crossUPnL;
  const totalEquity = crossEquity + activeIsoEquity;
  const availableMargin = Math.max(
    0,
    totalEquity - crossMarginUsed - isoMarginUsed - crossMM,
  );
  const maxWithdraw = Math.max(0, Math.min(cash, availableMargin));
  const crossLiquidated = hasActiveCross && crossEquity <= crossMM;

  return {
    crossEquity,
    totalEquity,
    availableMargin,
    crossMarginUsed,
    isoMarginUsed,
    crossUPnL,
    isoUPnL,
    crossMM,
    crossLiquidated,
    maxWithdraw,
    isoLosses,
  };
};

export const segMetrics = (
  eq: number[],
  forkIdx = 0,
): Pick<BranchMetrics, "ret" | "mdd" | "sharpe" | "val"> => {
  const segment = segmentValues(eq, forkIdx);

  if (segment.length === 0) {
    return { ret: 0, mdd: 0, sharpe: 0, val: 0 };
  }

  const ret = segment.length > 1 ? segment[segment.length - 1] / segment[0] - 1 : 0;

  let peak = segment[0];
  let worstDrawdown = 0;

  for (const value of segment) {
    peak = Math.max(peak, value);
    worstDrawdown = Math.min(worstDrawdown, value / peak - 1);
  }

  const returns: number[] = [];
  for (let index = 1; index < segment.length; index += 1) {
    returns.push(segment[index] / segment[index - 1] - 1);
  }

  const mean =
    returns.length > 0
      ? returns.reduce((sum, value) => sum + value, 0) / returns.length
      : 0;
  const variance =
    returns.length > 1
      ? returns.reduce((sum, value) => sum + (value - mean) ** 2, 0) /
        returns.length
      : 0;
  const stdDev = Math.sqrt(variance);
  const sharpe = stdDev > 0 ? (mean / stdDev) * Math.sqrt(252) : 0;

  return {
    ret,
    mdd: Math.abs(worstDrawdown),
    sharpe,
    val: segment[segment.length - 1],
  };
};

export const computeBranchEquity = (branch: Branch): BranchMetrics => {
  const forkIdx = branchForkIndex(branch);
  const eq = OHLC_DATES.map((_, index) =>
    index < forkIdx ? Number.NaN : accountStateAt(branch, index).totalEquity,
  );

  return {
    branch,
    eq,
    forkIdx,
    ...segMetrics(eq, forkIdx),
  };
};

export const computeAllBranches = (branches: Branch[]): BranchMetrics[] =>
  branches.map((branch) => computeBranchEquity(branch));

export const fmt = (value: number, digits = 2): string =>
  Number.isFinite(value)
    ? value.toLocaleString("en-US", {
        minimumFractionDigits: digits,
        maximumFractionDigits: digits,
      })
    : "—";

export const fmtK = (value: number, digits = 1): string => {
  if (!Number.isFinite(value)) {
    return "—";
  }

  const abs = Math.abs(value);

  if (abs >= 1_000_000_000) {
    return `${fmt(value / 1_000_000_000, digits)}B`;
  }

  if (abs >= 1_000_000) {
    return `${fmt(value / 1_000_000, digits)}M`;
  }

  if (abs >= 1_000) {
    return `${fmt(value / 1_000, digits)}K`;
  }

  return fmt(value, digits);
};

export const fmtP = (value: number, digits = 2): string => {
  if (!Number.isFinite(value)) {
    return "—";
  }

  const scaled = Math.abs(value) <= 1 ? value * 100 : value;
  const sign = scaled > 0 ? "+" : "";
  return `${sign}${fmt(scaled, digits)}%`;
};

export const fmtDate = (value: DateInput): string => {
  const date =
    typeof value === "number"
      ? OHLC_DATES[resolveIndex(value)]
      : typeof value === "string"
        ? value
        : value.toISOString();

  return new Date(date).toLocaleDateString("en-US", {
    year: "numeric",
    month: "short",
    day: "2-digit",
  });
};
