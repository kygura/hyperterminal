"use client";

import { useEffect, useMemo, useState } from "react";
import ReconnectingWebSocket from "reconnecting-websocket";
import type { Signal } from "@/lib/types";

export type SignalStreamStatus =
  | "connecting"
  | "connected"
  | "reconnecting"
  | "disconnected"
  | "error";

export interface SignalStreamState {
  signals: Signal[];
  status: SignalStreamStatus;
  streamUrl: string;
  latestSignalKey: string | null;
}

export const SIGNAL_STREAM_MAX_ROWS = 200;
export const DEFAULT_SIGNAL_STREAM_URL = "ws://localhost:8000/ws/signals";
export const SIGNAL_STREAM_STATUS_EVENT = "hypertrade:signal-stream-status";

export const SIGNAL_STATUS_META: Record<
  SignalStreamStatus,
  { label: string; toneClassName: string }
> = {
  connecting: {
    label: "Connecting",
    toneClassName: "text-[--amber]",
  },
  connected: {
    label: "Connected",
    toneClassName: "text-[--green]",
  },
  reconnecting: {
    label: "Reconnecting",
    toneClassName: "text-[--amber]",
  },
  disconnected: {
    label: "Disconnected",
    toneClassName: "text-[--text-secondary]",
  },
  error: {
    label: "Connection Error",
    toneClassName: "text-[--red]",
  },
};

export function resolveSignalStreamUrl(url?: string): string {
  return url ?? process.env.NEXT_PUBLIC_SIGNAL_WS_URL ?? DEFAULT_SIGNAL_STREAM_URL;
}

export function getSignalKey(signal: Signal): string {
  return [
    signal.id,
    signal.timestamp,
    signal.asset,
    signal.direction,
    signal.type,
    signal.meta.bid_volume,
    signal.meta.ask_volume,
    signal.meta.ratio,
    signal.meta.timeframe,
  ].join(":");
}

function isSignal(value: unknown): value is Signal {
  if (!value || typeof value !== "object") {
    return false;
  }

  const candidate = value as Partial<Signal>;

  return (
    typeof candidate.id === "string" &&
    typeof candidate.timestamp === "string" &&
    typeof candidate.asset === "string" &&
    (candidate.direction === "Long" || candidate.direction === "Short") &&
    typeof candidate.strength === "number" &&
    typeof candidate.type === "string" &&
    typeof candidate.meta?.bid_volume === "number" &&
    typeof candidate.meta?.ask_volume === "number" &&
    typeof candidate.meta?.ratio === "number" &&
    typeof candidate.meta?.timeframe === "string"
  );
}

export function parseSignalMessage(data: string | ArrayBuffer | Blob): Signal | null {
  if (typeof data !== "string") {
    return null;
  }

  try {
    const parsed: unknown = JSON.parse(data);
    return isSignal(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

export function useSignalStream(url?: string): SignalStreamState {
  const streamUrl = useMemo(() => resolveSignalStreamUrl(url), [url]);
  const [signals, setSignals] = useState<Signal[]>([]);
  const [status, setStatus] = useState<SignalStreamStatus>("connecting");
  const [latestSignalKey, setLatestSignalKey] = useState<string | null>(null);

  useEffect(() => {
    if (typeof window === "undefined") {
      return undefined;
    }

    let closedByCleanup = false;
    let hasConnected = false;

    setSignals([]);
    setLatestSignalKey(null);
    setStatus("connecting");

    const socket = new ReconnectingWebSocket(streamUrl, [], {
      WebSocket: window.WebSocket,
      connectionTimeout: 4000,
      minReconnectionDelay: 1000,
      maxReconnectionDelay: 10000,
      reconnectionDelayGrowFactor: 1.6,
      maxRetries: Infinity,
    });

    const handleOpen = () => {
      hasConnected = true;
      setStatus("connected");
    };

    const handleClose = () => {
      if (!closedByCleanup) {
        setStatus(hasConnected ? "reconnecting" : "disconnected");
      }
    };

    const handleError = () => {
      if (!closedByCleanup) {
        setStatus(hasConnected ? "reconnecting" : "error");
      }
    };

    const handleMessage = (event: MessageEvent<string | ArrayBuffer | Blob>) => {
      const signal = parseSignalMessage(event.data);

      if (!signal) {
        return;
      }

      const nextSignalKey = getSignalKey(signal);
      let added = false;

      setSignals((currentSignals) => {
        if (
          currentSignals.some(
            (currentSignal) => getSignalKey(currentSignal) === nextSignalKey,
          )
        ) {
          return currentSignals;
        }

        added = true;
        return [signal, ...currentSignals].slice(0, SIGNAL_STREAM_MAX_ROWS);
      });

      if (added) {
        setLatestSignalKey(nextSignalKey);
      }
    };

    socket.addEventListener("open", handleOpen);
    socket.addEventListener("close", handleClose);
    socket.addEventListener("error", handleError);
    socket.addEventListener("message", handleMessage);

    return () => {
      closedByCleanup = true;
      socket.removeEventListener("open", handleOpen);
      socket.removeEventListener("close", handleClose);
      socket.removeEventListener("error", handleError);
      socket.removeEventListener("message", handleMessage);
      socket.close(1000, "signal-stream-cleanup");
      setStatus("disconnected");
    };
  }, [streamUrl]);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }

    window.dispatchEvent(
      new CustomEvent(SIGNAL_STREAM_STATUS_EVENT, {
        detail: { status },
      }),
    );
  }, [status]);

  return {
    signals,
    status,
    streamUrl,
    latestSignalKey,
  };
}
