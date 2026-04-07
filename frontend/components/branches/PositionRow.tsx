"use client";

import { useEffect, useMemo, useState } from "react";

import { DerivedPositionState, fmt, fmtP, posStateAt } from "@/lib/margin-engine";
import { OHLC_ASSETS, OHLC_DATES, closeAt } from "@/lib/price-data";
import { ASSETS, Position } from "@/lib/types";

const FALLBACK_MAX_LEVERAGE = 20;
const EVALUATION_FALLBACK_DATE = OHLC_DATES[OHLC_DATES.length - 1] ?? "";

const joinClasses = (...values: Array<string | false | null | undefined>): string =>
  values.filter(Boolean).join(" ");

const clamp = (value: number, min: number, max: number): number =>
  Math.min(max, Math.max(min, value));

const directionBadgeStyle = (direction: Position["direction"]) =>
  direction === "Long"
    ? {
        color: "var(--green)",
        borderColor: "rgba(56, 166, 124, 0.35)",
        backgroundColor: "rgba(56, 166, 124, 0.12)",
      }
    : {
        color: "var(--red)",
        borderColor: "rgba(188, 38, 62, 0.35)",
        backgroundColor: "rgba(188, 38, 62, 0.12)",
      };

const modeBadgeStyle = (mode: Position["mode"]) =>
  mode === "Cross"
    ? {
        color: "var(--amber)",
        borderColor: "rgba(255, 184, 0, 0.35)",
        backgroundColor: "rgba(255, 184, 0, 0.12)",
      }
    : {
        color: "#50d2c1",
        borderColor: "rgba(80, 210, 193, 0.35)",
        backgroundColor: "rgba(80, 210, 193, 0.12)",
      };

interface PositionEditorDraft {
  asset: string;
  direction: Position["direction"];
  mode: Position["mode"];
  leverage: number;
  margin: number;
  entryDate: string;
  exitDate: string;
}

export interface PositionRowProps {
  assetOptions?: string[];
  className?: string;
  defaultExpanded?: boolean;
  evaluationDate?: number | string | Date;
  expanded?: boolean;
  onExpandedChange?: (expanded: boolean) => void;
  onRemove?: (positionId: string) => void;
  onUpdate?: (position: Position) => void;
  position: Position;
  state?: DerivedPositionState;
}

const draftFromPosition = (position: Position): PositionEditorDraft => ({
  asset: position.asset,
  direction: position.direction,
  mode: position.mode,
  leverage: position.leverage,
  margin: position.margin,
  entryDate: position.entryDate,
  exitDate: position.exitDate ?? "",
});

const assetMaxLeverage = (asset: string): number =>
  ASSETS[asset]?.maxLeverage ?? FALLBACK_MAX_LEVERAGE;

export default function PositionRow({
  assetOptions,
  className,
  defaultExpanded = false,
  evaluationDate,
  expanded,
  onExpandedChange,
  onRemove,
  onUpdate,
  position,
  state,
}: PositionRowProps) {
  const [internalExpanded, setInternalExpanded] = useState(defaultExpanded);
  const [draft, setDraft] = useState<PositionEditorDraft>(() => draftFromPosition(position));
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setDraft(draftFromPosition(position));
  }, [position]);

  const isExpanded = expanded ?? internalExpanded;
  const assets = assetOptions?.length
    ? assetOptions
    : OHLC_ASSETS.length
      ? OHLC_ASSETS
      : [position.asset];

  const setExpanded = (next: boolean): void => {
    if (expanded === undefined) {
      setInternalExpanded(next);
    }

    onExpandedChange?.(next);
  };

  const draftEntryPrice = useMemo(
    () => (draft.entryDate ? closeAt(draft.asset, draft.entryDate) : 0),
    [draft.asset, draft.entryDate],
  );
  const draftExitPrice = useMemo(
    () => (draft.exitDate ? closeAt(draft.asset, draft.exitDate) : undefined),
    [draft.asset, draft.exitDate],
  );

  const computedState = useMemo(() => {
    if (state) {
      return state;
    }

    return posStateAt(position, evaluationDate ?? position.exitDate ?? EVALUATION_FALLBACK_DATE);
  }, [evaluationDate, position, state]);

  const statusBadges = [
    computedState.isClosed
      ? {
          key: "closed",
          label: "Closed",
          style: {
            color: "var(--text-muted)",
            borderColor: "rgba(213, 209, 205, 0.35)",
            backgroundColor: "rgba(213, 209, 205, 0.1)",
          },
        }
      : null,
    computedState.isLiquidated
      ? {
          key: "liq",
          label: "Liq",
          style: {
            color: "var(--red-accent)",
            borderColor: "rgba(237, 54, 2, 0.35)",
            backgroundColor: "rgba(237, 54, 2, 0.12)",
          },
        }
      : null,
  ].filter(Boolean) as Array<{
    key: string;
    label: string;
    style: { backgroundColor: string; borderColor: string; color: string };
  }>;

  const handleSave = (): void => {
    if (!draft.entryDate) {
      setError("Entry date is required.");
      return;
    }

    if (draft.margin <= 0) {
      setError("Margin must be greater than zero.");
      return;
    }

    if (draft.entryDate && draftExitPrice !== undefined && draft.exitDate < draft.entryDate) {
      setError("Exit date must be on or after entry date.");
      return;
    }

    if (draftEntryPrice <= 0) {
      setError("Entry price is unavailable for the selected date.");
      return;
    }

    setError(null);
    onUpdate?.({
      ...position,
      asset: draft.asset,
      direction: draft.direction,
      mode: draft.mode,
      leverage: clamp(draft.leverage, 1, assetMaxLeverage(draft.asset)),
      margin: draft.margin,
      entryDate: draft.entryDate,
      entryPrice: draftEntryPrice,
      exitDate: draft.exitDate || undefined,
      exitPrice: draft.exitDate ? draftExitPrice : undefined,
    });
  };

  return (
    <article className={joinClasses("border border-[--border]", className)}>
      <button
        type="button"
        onClick={() => setExpanded(!isExpanded)}
        className="w-full bg-[--bg-panel] px-4 py-3 text-left transition hover:bg-[--bg-hover]"
      >
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[--border-subtle] pb-3">
          <div className="flex flex-wrap items-center gap-2">
            <span className="mono-data text-sm text-[--text-primary]">{position.asset}</span>
            <span
              className="ui-label border px-2 py-1"
              style={directionBadgeStyle(position.direction)}
            >
              {position.direction}
            </span>
            <span
              className="ui-label border px-2 py-1"
              style={modeBadgeStyle(position.mode)}
            >
              {position.mode}
            </span>
            <span className="ui-label border border-[--border] px-2 py-1 text-[--text-muted]">
              {position.leverage}×
            </span>
            {statusBadges.map((badge) => (
              <span
                key={badge.key}
                className="ui-label border px-2 py-1"
                style={badge.style}
              >
                {badge.label}
              </span>
            ))}
          </div>
          <span className="mono-data text-xs text-[--text-secondary]">
            {isExpanded ? "Hide details" : "Show details"}
          </span>
        </div>

        <div className="mt-3 grid gap-3 text-sm md:grid-cols-4 xl:grid-cols-7">
          <div>
            <p className="ui-label">Margin</p>
            <p className="mono-data mt-2 text-[--text-primary]">${fmt(position.margin)}</p>
          </div>
          <div>
            <p className="ui-label">Size</p>
            <p className="mono-data mt-2 text-[--text-primary]">${fmt(computedState.notional)}</p>
          </div>
          <div>
            <p className="ui-label">Entry</p>
            <p className="mono-data mt-2 text-[--text-primary]">${fmt(position.entryPrice)}</p>
          </div>
          <div>
            <p className="ui-label">Mark</p>
            <p className="mono-data mt-2 text-[--text-primary]">${fmt(computedState.mark)}</p>
          </div>
          <div>
            <p className="ui-label">PnL</p>
            <p
              className="mono-data mt-2"
              style={{
                color: computedState.displayPnl >= 0 ? "var(--green)" : "var(--red)",
              }}
            >
              ${fmt(computedState.displayPnl)}
            </p>
          </div>
          <div>
            <p className="ui-label">PnL %</p>
            <p
              className="mono-data mt-2"
              style={{
                color: computedState.pnlPct >= 0 ? "var(--green)" : "var(--red)",
              }}
            >
              {fmtP(computedState.pnlPct)}
            </p>
          </div>
          <div>
            <p className="ui-label">Liq</p>
            <p className="mono-data mt-2 text-[--text-primary]">${fmt(computedState.liqPrice)}</p>
          </div>
        </div>
      </button>

      {isExpanded ? (
        <div className="border-t border-[--border] bg-[--bg-panel-alt] p-4">
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
            <label className="space-y-2">
              <span className="ui-label">Asset</span>
              <select
                className="w-full px-3 py-2.5"
                value={draft.asset}
                onChange={(event) =>
                  setDraft((current) => ({
                    ...current,
                    asset: event.target.value,
                    leverage: clamp(current.leverage, 1, assetMaxLeverage(event.target.value)),
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
              <span className="ui-label">Leverage</span>
              <input
                className="w-full px-3 py-2.5"
                max={assetMaxLeverage(draft.asset)}
                min={1}
                onChange={(event) =>
                  setDraft((current) => ({
                    ...current,
                    leverage: clamp(Number(event.target.value), 1, assetMaxLeverage(current.asset)),
                  }))
                }
                type="number"
                value={draft.leverage}
              />
            </label>

            <label className="space-y-2">
              <span className="ui-label">Margin</span>
              <input
                className="w-full px-3 py-2.5"
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

            <div className="space-y-2">
              <span className="ui-label">Direction</span>
              <div className="grid grid-cols-2 gap-2">
                {(["Long", "Short"] as const).map((direction) => (
                  <button
                    key={direction}
                    type="button"
                    onClick={() => setDraft((current) => ({ ...current, direction }))}
                    className={joinClasses(
                      "ui-label border px-3 py-2.5",
                      draft.direction === direction
                        ? "text-[--text-primary]"
                        : "bg-[--bg-body] text-[--text-secondary]",
                    )}
                    style={
                      draft.direction === direction
                        ? {
                            backgroundColor:
                              direction === "Long" ? "var(--green)" : "var(--red)",
                            borderColor:
                              direction === "Long" ? "var(--green)" : "var(--red)",
                          }
                        : undefined
                    }
                  >
                    {direction}
                  </button>
                ))}
              </div>
            </div>

            <label className="space-y-2">
              <span className="ui-label">Entry date</span>
              <input
                className="w-full px-3 py-2.5"
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
                value={draftEntryPrice ? `$${fmt(draftEntryPrice)}` : "Unavailable"}
              />
            </label>

            <label className="space-y-2">
              <span className="ui-label">Exit date</span>
              <input
                className="w-full px-3 py-2.5"
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
                    ? draftExitPrice
                      ? `$${fmt(draftExitPrice)}`
                      : "Unavailable"
                    : "Open position"
                }
              />
            </label>
          </div>

          <div className="mt-3 grid grid-cols-2 gap-2 border border-[--border] p-3 sm:grid-cols-4">
            {(["Cross", "Isolated"] as const).map((mode) => (
              <button
                key={mode}
                type="button"
                onClick={() => setDraft((current) => ({ ...current, mode }))}
                className={joinClasses(
                  "ui-label border px-3 py-2.5",
                  draft.mode === mode
                    ? "bg-[--bg-elevated] text-[--text-primary]"
                    : "bg-[--bg-body] text-[--text-secondary]",
                )}
              >
                {mode}
              </button>
            ))}
          </div>

          <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
            {error ? (
              <p className="mono-data text-xs text-[--red]">{error}</p>
            ) : (
              <p className="mono-data text-xs text-[--text-secondary]">
                Qty {fmt(computedState.qty, 4)} · MM ${fmt(computedState.maintenanceMargin)}
              </p>
            )}

            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                onClick={() => {
                  setDraft(draftFromPosition(position));
                  setError(null);
                }}
                className="ui-label border border-[--border] px-3 py-2.5 text-[--text-secondary]"
              >
                Reset
              </button>
              <button
                type="button"
                onClick={() => onRemove?.(position.id)}
                className="ui-label border border-[--red] px-3 py-2.5 text-[--red]"
              >
                Remove
              </button>
              <button
                type="button"
                onClick={handleSave}
                className="ui-label border border-[--red-accent] bg-[--red-accent] px-3 py-2.5 text-[--text-primary]"
              >
                Save changes
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </article>
  );
}
