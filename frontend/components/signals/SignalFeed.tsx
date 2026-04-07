"use client";

import { useLayoutEffect, useMemo, useRef, useState } from "react";
import type { Signal } from "@/lib/types";
import type { SignalStreamStatus } from "@/lib/ws";
import { getSignalKey, SIGNAL_STATUS_META } from "@/lib/ws";
import { SignalRow } from "./SignalRow";

const SIGNAL_COLUMNS = [
  "Time",
  "Asset",
  "Dir",
  "Conviction",
  "Regime",
  "Signals",
  "Price",
  "VWAP",
] as const;

interface SignalFeedProps {
  signals: Signal[];
  status: SignalStreamStatus;
  latestSignalKey: string | null;
}

export function SignalFeed({
  signals,
  status,
  latestSignalKey,
}: SignalFeedProps) {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const previousHeightRef = useRef(0);
  const previousCountRef = useRef(0);
  const [autoFollow, setAutoFollow] = useState(true);
  const [pendingSignals, setPendingSignals] = useState(0);

  const statusMeta = SIGNAL_STATUS_META[status];

  const emptyStateLabel = useMemo(() => {
    if (status === "connected" || status === "reconnecting") {
      return "Waiting for the next signal print";
    }

    if (status === "error") {
      return "Unable to parse or receive the signal stream";
    }

    return "Opening signal stream";
  }, [status]);

  useLayoutEffect(() => {
    const container = scrollRef.current;

    if (!container) {
      return;
    }

    const nextHeight = container.scrollHeight;
    const previousHeight = previousHeightRef.current;
    const nextCount = signals.length;
    const countDelta = nextCount - previousCountRef.current;

    if (countDelta > 0) {
      if (autoFollow) {
        container.scrollTop = 0;
        setPendingSignals(0);
      } else {
        container.scrollTop += Math.max(nextHeight - previousHeight, 0);
        setPendingSignals((currentCount) => currentCount + countDelta);
      }
    }

    previousHeightRef.current = nextHeight;
    previousCountRef.current = nextCount;
  }, [autoFollow, signals]);

  const handleScroll = () => {
    const container = scrollRef.current;

    if (!container) {
      return;
    }

    const shouldFollow = container.scrollTop <= 16;
    setAutoFollow(shouldFollow);

    if (shouldFollow) {
      setPendingSignals(0);
    }
  };

  const jumpToNewest = () => {
    const container = scrollRef.current;

    if (!container) {
      return;
    }

    container.scrollTo({ top: 0, behavior: "smooth" });
    setAutoFollow(true);
    setPendingSignals(0);
  };

  return (
    <div className="panel overflow-hidden">
      <div className="grid grid-cols-[0.95fr_0.8fr_0.7fr_0.9fr_1.25fr_1.8fr_0.9fr_0.9fr] gap-3 border-b border-[--border] bg-[--bg-panel-alt] px-4 py-3">
        {SIGNAL_COLUMNS.map((column) => (
          <span key={column} className="ui-label">
            {column}
          </span>
        ))}
      </div>

      <div className="relative">
        {!autoFollow && pendingSignals > 0 ? (
          <button
            type="button"
            onClick={jumpToNewest}
            className="absolute right-4 top-3 z-10 border border-[rgba(237,54,2,0.3)] bg-[rgba(237,54,2,0.12)] px-3 py-1.5 font-mono text-[11px] uppercase tracking-[0.12em] text-[--text-primary] transition hover:bg-[rgba(237,54,2,0.18)]"
          >
            New signals ↓ {pendingSignals}
          </button>
        ) : null}

        <div
          ref={scrollRef}
          onScroll={handleScroll}
          className="max-h-[calc(100vh-15rem)] min-h-[28rem] overflow-auto"
        >
          {signals.length > 0 ? (
            signals.map((signal) => (
              <SignalRow
                key={getSignalKey(signal)}
                signal={signal}
                isFresh={latestSignalKey !== null && latestSignalKey === getSignalKey(signal)}
              />
            ))
          ) : (
            <div className="flex min-h-[28rem] items-center justify-center px-6">
              <div className="space-y-3 text-center">
                <p className={`mono-data text-xs uppercase tracking-[0.16em] ${statusMeta.toneClassName}`}>
                  {statusMeta.label}
                </p>
                <p className="mono-data text-sm text-[--text-secondary]">
                  {emptyStateLabel}
                </p>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export default SignalFeed;
