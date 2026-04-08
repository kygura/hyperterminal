"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
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

function formatPrice(value: number | null): string {
  if (value === null) {
    return "—";
  }

  return new Intl.NumberFormat("en-US", {
    maximumFractionDigits: value >= 1_000 ? 0 : 2,
  }).format(value);
}

function formatTypeLabel(type: string): string {
  return type.replaceAll("_", " ");
}

function convictionTone(conviction: Signal["conviction"]): string {
  if (conviction === "HIGH") {
    return "bg-[rgba(188,38,62,0.10)] text-[--red]";
  }
  if (conviction === "MEDIUM") {
    return "bg-[rgba(237,166,0,0.10)] text-[--amber]";
  }
  return "bg-[rgba(56,166,124,0.10)] text-[--green]";
}

export function SignalRow({ signal, isFresh = false }: SignalRowProps) {
  const router = useRouter();
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
  const convictionToneClass = convictionTone(signal.conviction);
  const renderedSignals =
    signal.signals.length > 0
      ? signal.signals.map((entry) => formatTypeLabel(entry)).join(", ")
      : `${signal.signalCount} active`;

  const handleRowClick = () => {
    const params = new URLSearchParams();
    params.set("asset", signal.asset);
    params.set("time", new Date(signal.timestamp).getTime().toString());
    params.set("dir", signal.direction);
    if (signal.price) {
      params.set("price", signal.price.toString());
    }
    router.push(`/charts?${params.toString()}`);
  };

  return (
    <div
      onClick={handleRowClick}
      className={[
        "cursor-pointer grid grid-cols-[0.95fr_0.8fr_0.7fr_0.9fr_1.25fr_1.8fr_0.9fr_0.9fr] gap-3 border-b border-[--border-subtle] px-4 py-2.5 text-[12px] transition-all duration-300 ease-out last:border-b-0 hover:bg-[--bg-hover]",
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
        <span className={`inline-flex min-w-[58px] items-center justify-center px-2 py-1 text-[11px] font-medium uppercase tracking-[0.08em] ${directionTone}`}>
          {signal.direction}
        </span>
      </span>

      <span className="min-w-0">
        <span className={`inline-flex min-w-[74px] items-center justify-center px-2 py-1 text-[11px] font-medium uppercase tracking-[0.08em] ${convictionToneClass}`}>
          {signal.conviction}
        </span>
      </span>

      <span className="min-w-0">
        <span className="block truncate font-mono text-[11px] uppercase tracking-[0.1em] text-[--text-muted]">
          {formatTypeLabel(signal.regime)}
        </span>
        <span className="ui-label mt-1 block text-[10px] text-[--text-secondary]">
          {signal.timeframe}
        </span>
      </span>

      <span className="ui-label truncate text-[10px] leading-5 text-[--text-secondary]">
        {renderedSignals}
      </span>

      <span className="mono-data text-[--text-primary]">
        {formatPrice(signal.price)}
      </span>

      <span className="mono-data text-[--text-primary]">
        {formatPrice(signal.vwap)}
      </span>
    </div>
  );
}

export default SignalRow;
