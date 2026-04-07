"use client";

import { useEffect, useMemo, useState } from "react";

import { fmt, maintenanceMarginRate, posStateAt } from "@/lib/margin-engine";
import { OHLC_ASSETS, OHLC_DATES, closeAt } from "@/lib/price-data";
import { ASSETS, Position } from "@/lib/types";

const FALLBACK_MAX_LEVERAGE = 20;
const DEFAULT_MARGIN = 1_000;

const joinClasses = (...values: Array<string | false | null | undefined>): string =>
  values.filter(Boolean).join(" ");

const clamp = (value: number, min: number, max: number): number =>
  Math.min(max, Math.max(min, value));

const priceFor = (asset: string, date: string): number => {
  if (!date) {
    return 0;
  }

  return closeAt(asset, date);
};

const defaultEntryDate = (): string => {
  const fallbackIndex = Math.max(0, OHLC_DATES.length - 14);
  return OHLC_DATES[fallbackIndex] ?? OHLC_DATES[0] ?? "";
};

export interface PositionEntryDraft {
  asset: string;
  direction: Position["direction"];
  mode: Position["mode"];
  leverage: number;
  margin: number;
  entryDate: string;
  exitDate: string;
}

export interface PositionEntryMetrics {
  entryPrice: number;
  exitPrice?: number;
  markPrice: number;
  liquidationPrice: number;
  notional: number;
  marginRequired: number;
  maintenance: number;
  qty: number;
  maxLeverage: number;
  maintenanceRate: number;
}

export interface PositionEntryChange extends PositionEntryDraft {
  metrics: PositionEntryMetrics;
}

export interface PositionEntrySubmit
  extends Omit<Position, "id" | "entryPrice" | "exitPrice"> {
  entryPrice: number;
  exitPrice?: number;
}

export interface PositionEntryProps {
  assetOptions?: string[];
  className?: string;
  defaultValues?: Partial<PositionEntryDraft>;
  disabled?: boolean;
  onChange?: (payload: PositionEntryChange) => void;
  onSubmit?: (payload: PositionEntrySubmit) => void;
  submitLabel?: string;
}

const defaultAsset = (): string => OHLC_ASSETS[0] ?? "BTC";

const defaultDraft = (overrides?: Partial<PositionEntryDraft>): PositionEntryDraft => ({
  asset: overrides?.asset ?? defaultAsset(),
  direction: overrides?.direction ?? "Long",
  mode: overrides?.mode ?? "Cross",
  leverage: overrides?.leverage ?? 10,
  margin: overrides?.margin ?? DEFAULT_MARGIN,
  entryDate: overrides?.entryDate ?? defaultEntryDate(),
  exitDate: overrides?.exitDate ?? "",
});

const assetConfigFor = (asset: string) =>
  ASSETS[asset] ?? {
    name: asset,
    color: "var(--text-muted)",
    maxLeverage: FALLBACK_MAX_LEVERAGE,
  };

export default function PositionEntry({
  assetOptions,
  className,
  defaultValues,
  disabled = false,
  onChange,
  onSubmit,
  submitLabel,
}: PositionEntryProps) {
  const [draft, setDraft] = useState<PositionEntryDraft>(() => defaultDraft(defaultValues));
  const [showLeverageSlider, setShowLeverageSlider] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const latestDate = OHLC_DATES[OHLC_DATES.length - 1] ?? "";

  useEffect(() => {
    setDraft(defaultDraft(defaultValues));
  }, [defaultValues, latestDate]);

  const assets = useMemo(() => {
    const source = assetOptions?.length ? assetOptions : OHLC_ASSETS;
    return source.length ? source : [defaultAsset()];
  }, [assetOptions]);

  const assetConfig = assetConfigFor(draft.asset);
  const maxLeverage = assetConfig.maxLeverage;

  useEffect(() => {
    setDraft((current) => {
      const nextAsset = assets.includes(current.asset) ? current.asset : assets[0];
      const nextLeverage = clamp(current.leverage, 1, assetConfigFor(nextAsset).maxLeverage);

      if (nextAsset === current.asset && nextLeverage === current.leverage) {
        return current;
      }

      return {
        ...current,
        asset: nextAsset,
        leverage: nextLeverage,
      };
    });
  }, [assets]);

  const metrics = useMemo<PositionEntryMetrics>(() => {
    const entryPrice = priceFor(draft.asset, draft.entryDate);
    const exitPrice = draft.exitDate ? priceFor(draft.asset, draft.exitDate) : undefined;
    const previewState =
      entryPrice > 0
        ? posStateAt(
            {
              id: "draft",
              asset: draft.asset,
              direction: draft.direction,
              mode: draft.mode,
              leverage: draft.leverage,
              margin: draft.margin,
              entryDate: draft.entryDate,
              entryPrice,
              exitDate: draft.exitDate || undefined,
              exitPrice,
            },
            draft.entryDate,
          )
        : null;

    return {
      entryPrice,
      exitPrice,
      markPrice: priceFor(draft.asset, OHLC_DATES[OHLC_DATES.length - 1] ?? draft.entryDate),
      liquidationPrice: previewState?.liqPrice ?? 0,
      notional: previewState?.notional ?? draft.margin * draft.leverage,
      marginRequired: draft.margin,
      maintenance: previewState?.maintenanceMargin ?? 0,
      qty: previewState?.qty ?? 0,
      maxLeverage,
      maintenanceRate: maintenanceMarginRate(draft.leverage),
    };
  }, [draft, maxLeverage]);

  useEffect(() => {
    onChange?.({
      ...draft,
      metrics,
    });
  }, [draft, metrics, onChange]);

  const actionLabel = submitLabel ?? `Add ${draft.direction} ${draft.asset}`;

  const handleSubmit = (): void => {
    const normalizedMargin = Number(draft.margin);

    if (!draft.entryDate) {
      setError("Entry date is required.");
      return;
    }

    if (normalizedMargin <= 0) {
      setError("Margin must be greater than zero.");
      return;
    }

    if (metrics.entryPrice <= 0) {
      setError("Entry price is unavailable for the selected date.");
      return;
    }

    if (draft.exitDate && draft.exitDate < draft.entryDate) {
      setError("Exit date must be on or after the entry date.");
      return;
    }

    setError(null);

    onSubmit?.({
      asset: draft.asset,
      direction: draft.direction,
      mode: draft.mode,
      leverage: draft.leverage,
      margin: normalizedMargin,
      entryDate: draft.entryDate,
      entryPrice: metrics.entryPrice,
      exitDate: draft.exitDate || undefined,
      exitPrice: draft.exitDate ? metrics.exitPrice : undefined,
    });
  };

  return (
    <section className={joinClasses("panel p-4", className)}>
      <div className="flex items-start justify-between gap-4 border-b border-[--border] pb-4">
        <div>
          <p className="section-header">Position entry</p>
          <p className="mono-data mt-2 text-xs text-[--text-secondary]">
            Build a branch position with margin as collateral and notional derived from leverage.
          </p>
        </div>
        <span className="mono-data text-xs text-[--text-secondary]">
          Mark {draft.asset} ${fmt(metrics.markPrice)}
        </span>
      </div>

      <div className="mt-4 space-y-4">
        <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_140px]">
          <div className="grid grid-cols-2 border border-[--border]">
            {(["Cross", "Isolated"] as const).map((mode) => (
              <button
                key={mode}
                type="button"
                disabled={disabled}
                onClick={() => setDraft((current) => ({ ...current, mode }))}
                className={joinClasses(
                  "ui-label px-3 py-2.5 transition",
                  draft.mode === mode
                    ? "bg-[--bg-elevated] text-[--text-primary]"
                    : "bg-[--bg-body] text-[--text-secondary] hover:bg-[--bg-hover]",
                )}
              >
                {mode}
              </button>
            ))}
          </div>

          <button
            type="button"
            disabled={disabled}
            onClick={() => setShowLeverageSlider((current) => !current)}
            className="flex items-center justify-between border border-[--border] bg-[--bg-body] px-3 py-2.5"
          >
            <span className="ui-label">Leverage</span>
            <span className="mono-data text-sm text-[--text-primary]">
              {draft.leverage}×
            </span>
          </button>
        </div>

        {showLeverageSlider ? (
          <div className="border border-[--border] bg-[--bg-panel-alt] px-4 py-3">
            <div className="flex items-center justify-between gap-3">
              <span className="ui-label">{draft.asset} max</span>
              <span className="mono-data text-sm text-[--text-primary]">
                {maxLeverage}×
              </span>
            </div>
            <input
              className="mt-3 h-2 w-full accent-[--red-accent]"
              disabled={disabled}
              max={maxLeverage}
              min={1}
              onChange={(event) =>
                setDraft((current) => ({
                  ...current,
                  leverage: clamp(Number(event.target.value), 1, maxLeverage),
                }))
              }
              type="range"
              value={draft.leverage}
            />
          </div>
        ) : null}

        <div className="grid grid-cols-2 gap-3">
          {(["Long", "Short"] as const).map((direction) => {
            const active = draft.direction === direction;
            const tone = direction === "Long" ? "var(--green)" : "var(--red)";

            return (
              <button
                key={direction}
                type="button"
                disabled={disabled}
                onClick={() => setDraft((current) => ({ ...current, direction }))}
                className={joinClasses(
                  "ui-label w-full border px-3 py-3 transition",
                  active
                    ? "text-[--text-primary]"
                    : "bg-[--bg-body] text-[--text-secondary] hover:bg-[--bg-hover]",
                )}
                style={
                  active
                    ? {
                        backgroundColor: tone,
                        borderColor: tone,
                      }
                    : undefined
                }
              >
                {direction}
              </button>
            );
          })}
        </div>

        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          <label className="space-y-2">
            <span className="ui-label">Asset</span>
            <select
              className="w-full px-3 py-2.5"
              disabled={disabled}
              value={draft.asset}
              onChange={(event) =>
                setDraft((current) => ({
                  ...current,
                  asset: event.target.value,
                  leverage: clamp(current.leverage, 1, assetConfigFor(event.target.value).maxLeverage),
                }))
              }
            >
              {assets.map((asset) => (
                <option key={asset} value={asset}>
                  {asset}
                </option>
              ))}
            </select>
          </label>

          <label className="space-y-2">
            <span className="ui-label">Margin</span>
            <input
              className="w-full px-3 py-2.5"
              disabled={disabled}
              min={0}
              onChange={(event) =>
                setDraft((current) => ({
                  ...current,
                  margin: Number(event.target.value),
                }))
              }
              step="100"
              type="number"
              value={draft.margin}
            />
          </label>

          <label className="space-y-2">
            <span className="ui-label">Entry date</span>
            <input
              className="w-full px-3 py-2.5"
              disabled={disabled}
              max={OHLC_DATES[OHLC_DATES.length - 1]}
              min={OHLC_DATES[0]}
              onChange={(event) =>
                setDraft((current) => ({
                  ...current,
                  entryDate: event.target.value,
                }))
              }
              type="date"
              value={draft.entryDate}
            />
          </label>

          <label className="space-y-2">
            <span className="ui-label">Entry price</span>
            <input
              className="w-full px-3 py-2.5 text-[--text-muted]"
              readOnly
              type="text"
              value={metrics.entryPrice ? `$${fmt(metrics.entryPrice)}` : "Unavailable"}
            />
          </label>

          <label className="space-y-2">
            <span className="ui-label">Exit date</span>
            <input
              className="w-full px-3 py-2.5"
              disabled={disabled}
              max={OHLC_DATES[OHLC_DATES.length - 1]}
              min={draft.entryDate || OHLC_DATES[0]}
              onChange={(event) =>
                setDraft((current) => ({
                  ...current,
                  exitDate: event.target.value,
                }))
              }
              type="date"
              value={draft.exitDate}
            />
          </label>

          <label className="space-y-2">
            <span className="ui-label">Exit price</span>
            <input
              className="w-full px-3 py-2.5 text-[--text-muted]"
              readOnly
              type="text"
              value={
                draft.exitDate
                  ? metrics.exitPrice
                    ? `$${fmt(metrics.exitPrice)}`
                    : "Unavailable"
                  : "Open position"
              }
            />
          </label>
        </div>

        <div className="grid gap-3 border border-[--border] bg-[--bg-panel-alt] p-4 md:grid-cols-2 xl:grid-cols-4">
          <div>
            <p className="ui-label">Liquidation price</p>
            <p className="mono-data mt-2 text-sm text-[--text-primary]">
              ${fmt(metrics.liquidationPrice)}
            </p>
          </div>
          <div>
            <p className="ui-label">Notional</p>
            <p className="mono-data mt-2 text-sm text-[--text-primary]">
              ${fmt(metrics.notional)}
            </p>
          </div>
          <div>
            <p className="ui-label">Margin required</p>
            <p className="mono-data mt-2 text-sm text-[--text-primary]">
              ${fmt(metrics.marginRequired)}
            </p>
          </div>
          <div>
            <p className="ui-label">Maintenance</p>
            <p className="mono-data mt-2 text-sm text-[--text-primary]">
              ${fmt(metrics.maintenance)}
            </p>
          </div>
        </div>

        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-[--border] pt-4">
          <div className="mono-data flex flex-wrap gap-4 text-xs text-[--text-secondary]">
            <span>QTY {fmt(metrics.qty, 4)}</span>
            <span>MMR {(metrics.maintenanceRate * 100).toFixed(2)}%</span>
            <span>{assetConfig.name}</span>
          </div>

          <button
            type="button"
            disabled={disabled}
            onClick={handleSubmit}
            className="ui-label min-w-[220px] border px-4 py-3 text-[--text-primary]"
            style={{
              backgroundColor: draft.direction === "Long" ? "var(--green)" : "var(--red)",
              borderColor: draft.direction === "Long" ? "var(--green)" : "var(--red)",
            }}
          >
            {actionLabel}
          </button>
        </div>

        {error ? (
          <p className="mono-data text-xs text-[--red]">{error}</p>
        ) : (
          <p className="mono-data text-xs text-[--text-secondary]">
            Entry auto-prices from {draft.entryDate || "—"} and keeps leverage capped at{" "}
            {maxLeverage}×.
          </p>
        )}
      </div>
    </section>
  );
}
