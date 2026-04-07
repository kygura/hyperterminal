"use client";

import { useEffect, useState } from "react";
import { ASSETS, type Signal } from "@/lib/types";

interface SignalRowProps {
  signal: Signal;
  isFresh?: boolean;
}

function formatSignalTime(timestamp: string): string {
  const date = new Date(timestamp);

  if (Number.isNaN(date.getTime())) {
    return timestamp;
  }

  return date
    .toLocaleTimeString("en-GB", {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      timeZone: "UTC",
    })
    .replace(/:/g, ":")
    .concat("Z");
}

function formatCompactVolume(value: number): string {
  return new Intl.NumberFormat("en-US", {
    notation: "compact",
    maximumFractionDigits: value >= 1_000_000 ? 1 : 0,
  }).format(value);
}

function formatRatio(value: number): string {
  return value.toFixed(2);
}

function formatTypeLabel(type: string): string {
  return type.replaceAll("_", " ");
}

export function SignalRow({ signal, isFresh = false }: SignalRowProps) {
  const [entered, setEntered] = useState(!isFresh);
  const [highlighted, setHighlighted] = useState(isFresh);

  useEffect(() => {
    if (!isFresh) {
      return;
    }

    setEntered(false);
    setHighlighted(true);

    const frame = requestAnimationFrame(() => {
      setEntered(true);
    });

    const timeout = window.setTimeout(() => {
      setHighlighted(false);
    }, 1400);

    return () => {
      cancelAnimationFrame(frame);
      window.clearTimeout(timeout);
    };
  }, [isFresh]);

  const assetConfig = ASSETS[signal.asset];
  const directionTone =
    signal.direction === "Long"
      ? "bg-[rgba(56,166,124,0.10)] text-[--green]"
      : "bg-[rgba(188,38,62,0.10)] text-[--red]";

  return (
    <div
      className={[
        "grid grid-cols-[0.95fr_0.8fr_0.7fr_0.85fr_1.7fr_1fr_1fr_0.75fr] gap-3 border-b border-[--border-subtle] px-4 py-2.5 text-[12px] transition-all duration-300 ease-out last:border-b-0",
        entered ? "translate-y-0 opacity-100" : "-translate-y-1 opacity-0",
        highlighted ? "bg-[rgba(237,54,2,0.06)]" : "bg-transparent",
      ].join(" ")}
    >
      <span className="mono-data text-[--text-secondary]">
        {formatSignalTime(signal.timestamp)}
      </span>

      <span className="flex min-w-0 items-center gap-2">
        <span
          className="h-2 w-2 shrink-0 rounded-full"
          style={{ backgroundColor: assetConfig?.color ?? "var(--text-muted)" }}
        />
        <span className="mono-data truncate text-[--text-primary]">{signal.asset}</span>
      </span>

      <span className="min-w-0">
        <span
          className={`inline-flex min-w-[58px] items-center justify-center px-2 py-1 text-[11px] font-medium uppercase tracking-[0.08em] ${directionTone}`}
        >
          {signal.direction}
        </span>
      </span>

      <span className="mono-data text-[--text-primary]">
        {signal.strength.toFixed(2)}
      </span>

      <span className="min-w-0">
        <span className="block truncate font-mono text-[11px] uppercase tracking-[0.1em] text-[--text-muted]">
          {formatTypeLabel(signal.type)}
        </span>
        <span className="ui-label mt-1 block text-[10px] text-[--text-secondary]">
          {signal.meta.timeframe}
        </span>
      </span>

      <span className="mono-data text-[--text-primary]">
        {formatCompactVolume(signal.meta.bid_volume)}
      </span>

      <span className="mono-data text-[--text-primary]">
        {formatCompactVolume(signal.meta.ask_volume)}
      </span>

      <span
        className={`mono-data ${
          signal.meta.ratio >= 1 ? "text-[--green]" : "text-[--red]"
        }`}
      >
        {formatRatio(signal.meta.ratio)}
      </span>
    </div>
  );
}

export default SignalRow;
