import rawData from "@/lib/data/ohlc.json";
import { ASSETS } from "@/lib/types";

export interface Candle {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface OhlcDataset {
  generatedAt: string;
  days: number;
  timeframe?: string;
  cutoffDate?: string | null;
  assets: Record<string, Candle[]>;
}

export type PriceField = "open" | "high" | "low" | "close";
export type DateLike = string | Date;

const fallbackDataset = rawData as OhlcDataset;

let dateToExactIndex = new Map<string, number>();

export let OHLC_DATA: OhlcDataset = fallbackDataset;
export let OHLC_ASSETS: string[] = [];
export let DEFAULT_ASSET = "BTC";
export let OHLC_DATES: string[] = [];

const refreshExports = (): void => {
  OHLC_ASSETS = Object.keys(ASSETS).filter((asset) => asset in OHLC_DATA.assets);
  DEFAULT_ASSET = OHLC_ASSETS[0] ?? "BTC";
  OHLC_DATES = OHLC_DATA.assets[DEFAULT_ASSET]?.map((candle) => candle.date) ?? [];
  dateToExactIndex = new Map(OHLC_DATES.map((date, index) => [date, index]));
};

refreshExports();

const mergeCandles = (current: Candle[] = [], incoming: Candle[] = []): Candle[] => {
  const byDate = new Map(current.map((candle) => [candle.date, candle]));

  for (const candle of incoming) {
    byDate.set(candle.date, candle);
  }

  return [...byDate.values()].sort((left, right) => left.date.localeCompare(right.date));
};

export const setOhlcDataset = (dataset: OhlcDataset): void => {
  const nextAssets = Object.fromEntries(
    Object.entries(dataset.assets ?? {}).filter(([, candles]) => Array.isArray(candles) && candles.length > 0),
  );
  const mergedAssets = { ...OHLC_DATA.assets };

  for (const [asset, candles] of Object.entries(nextAssets)) {
    mergedAssets[asset] = mergeCandles(mergedAssets[asset], candles);
  }

  OHLC_DATA = {
    generatedAt: dataset.generatedAt || OHLC_DATA.generatedAt,
    days: Math.max(dataset.days || 0, OHLC_DATA.days),
    assets: mergedAssets,
  };

  refreshExports();
};

const normalizeDate = (value: DateLike): string =>
  typeof value === "string" ? value.slice(0, 10) : value.toISOString().slice(0, 10);

const clampIndex = (index: number): number => {
  if (OHLC_DATES.length === 0) {
    return 0;
  }

  return Math.max(0, Math.min(OHLC_DATES.length - 1, index));
};

const binarySearch = (target: string, mode: "before" | "after"): number => {
  if (OHLC_DATES.length === 0) {
    return 0;
  }

  let low = 0;
  let high = OHLC_DATES.length - 1;
  let answer = mode === "after" ? OHLC_DATES.length - 1 : 0;

  while (low <= high) {
    const mid = Math.floor((low + high) / 2);
    const probe = OHLC_DATES[mid];

    if (probe === target) {
      return mid;
    }

    if (probe < target) {
      if (mode === "before") {
        answer = mid;
      }
      low = mid + 1;
    } else {
      if (mode === "after") {
        answer = mid;
      }
      high = mid - 1;
    }
  }

  return clampIndex(answer);
};

export const hasAsset = (asset: string): boolean => asset in OHLC_DATA.assets;

export const candlesForAsset = (asset: string): Candle[] =>
  OHLC_DATA.assets[asset] ?? OHLC_DATA.assets[DEFAULT_ASSET] ?? [];

export const resolveIndex = (at: number | DateLike, mode: "before" | "after" = "before"): number => {
  if (typeof at === "number") {
    return clampIndex(at);
  }

  const date = normalizeDate(at);
  return dateToExactIndex.get(date) ?? binarySearch(date, mode);
};

export const indexAtOrBefore = (at: DateLike): number => resolveIndex(at, "before");

export const indexAtOrAfter = (at: DateLike): number => resolveIndex(at, "after");

export const dateAt = (index: number): string =>
  OHLC_DATES[clampIndex(index)] ?? "";

export const candleAt = (asset: string, at: number | DateLike): Candle | undefined =>
  candlesForAsset(asset)[resolveIndex(at)];

export const priceAt = (
  asset: string,
  at: number | DateLike,
  field: PriceField = "close",
): number => candleAt(asset, at)?.[field] ?? 0;

export const openAt = (asset: string, at: number | DateLike): number =>
  priceAt(asset, at, "open");

export const highAt = (asset: string, at: number | DateLike): number =>
  priceAt(asset, at, "high");

export const lowAt = (asset: string, at: number | DateLike): number =>
  priceAt(asset, at, "low");

export const closeAt = (asset: string, at: number | DateLike): number =>
  priceAt(asset, at, "close");

export const latestClose = (asset: string): number => {
  const series = candlesForAsset(asset);
  return series[series.length - 1]?.close ?? 0;
};

export const candleRange = (
  asset: string,
  start: number | DateLike,
  end: number | DateLike,
): Candle[] => {
  const from = resolveIndex(start, "after");
  const to = resolveIndex(end, "before");
  return candlesForAsset(asset).slice(from, to + 1);
};
