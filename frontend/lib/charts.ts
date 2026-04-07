import { PineTS } from "pinets";
import type { Candle } from "@/lib/price-data";

export interface QFChartCandle {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface PineTSCandle {
  openTime: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  closeTime: number;
}

export interface SavedIndicator {
  name: string;
  code: string;
}

const STORAGE_KEY = "hypertrade:indicators";
const DAY_MS = 86_400_000;

const dateToMs = (date: string): number => new Date(date).getTime();

export const candlesToQFChart = (candles: Candle[]): QFChartCandle[] =>
  candles.map((c) => ({
    time: dateToMs(c.date),
    open: c.open,
    high: c.high,
    low: c.low,
    close: c.close,
    volume: c.volume,
  }));

export const candlesToPineTS = (candles: Candle[]): PineTSCandle[] =>
  candles.map((c) => {
    const openTime = dateToMs(c.date);
    return {
      openTime,
      open: c.open,
      high: c.high,
      low: c.low,
      close: c.close,
      volume: c.volume,
      closeTime: openTime + DAY_MS,
    };
  });

export async function runIndicator(
  code: string,
  candles: Candle[],
): Promise<{ plots: Record<string, unknown> | null; error: string | null }> {
  try {
    const data = candlesToPineTS(candles);
    const pine = new PineTS(data);
    const ctx = await pine.run(code);
    return { plots: ctx.plots ?? null, error: null };
  } catch (err: unknown) {
    const message = err instanceof Error ? err.message : String(err);
    return { plots: null, error: message };
  }
}

export const loadIndicators = (): SavedIndicator[] => {
  if (typeof window === "undefined") return [];
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as SavedIndicator[]) : [];
  } catch {
    return [];
  }
};

export const saveIndicator = (name: string, code: string): void => {
  const list = loadIndicators();
  const idx = list.findIndex((i) => i.name === name);
  if (idx >= 0) {
    list[idx] = { name, code };
  } else {
    list.push({ name, code });
  }
  localStorage.setItem(STORAGE_KEY, JSON.stringify(list));
};

export const deleteIndicator = (name: string): void => {
  const list = loadIndicators().filter((i) => i.name !== name);
  localStorage.setItem(STORAGE_KEY, JSON.stringify(list));
};
