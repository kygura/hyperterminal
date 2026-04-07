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
export const DEFAULT_API_URL = "http://localhost:8000";
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

export function resolveApiUrl(url?: string): string {
  return (url ?? process.env.NEXT_PUBLIC_API_URL ?? DEFAULT_API_URL).replace(/\/$/, "");
}

export function getSignalKey(signal: Signal): string {
  return [
    signal.id,
    signal.timestamp,
    signal.asset,
    signal.direction,
    signal.conviction,
    signal.regime,
    signal.signalCount,
    signal.timeframe,
  ].join(":");
}

function normalizeDirection(value: unknown): Signal["direction"] | null {
  const direction = String(value ?? "").toUpperCase();
  if (direction.includes("LONG")) {
    return "Long";
  }
  if (direction.includes("SHORT")) {
    return "Short";
  }
  return null;
}

function normalizeTimestamp(value: unknown): string | null {
  if (typeof value === "number" && Number.isFinite(value)) {
    return new Date(value).toISOString();
  }
  if (typeof value === "string") {
    const parsed = new Date(value);
    if (!Number.isNaN(parsed.getTime())) {
      return parsed.toISOString();
    }
  }
  return null;
}

function normalizeSignals(value: unknown): string[] {
  if (Array.isArray(value)) {
    return value.filter((entry): entry is string => typeof entry === "string");
  }
  if (typeof value === "string") {
    try {
      const parsed: unknown = JSON.parse(value);
      if (Array.isArray(parsed)) {
        return parsed.filter((entry): entry is string => typeof entry === "string");
      }
    } catch {
      return [];
    }
  }
  return [];
}

function normalizeNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function normalizeSignalRecord(value: unknown): Signal | null {
  if (!value || typeof value !== "object") {
    return null;
  }

  const candidate = value as Record<string, unknown>;
  const direction = normalizeDirection(candidate.direction);
  const timestamp = normalizeTimestamp(candidate.ts ?? candidate.timestamp);

  if (!direction || !timestamp || typeof candidate.asset !== "string") {
    return null;
  }

  const signals = normalizeSignals(candidate.signals_json ?? candidate.signals);
  const legacyMeta =
    candidate.meta && typeof candidate.meta === "object"
      ? (candidate.meta as Record<string, unknown>)
      : null;
  const signalCount =
    typeof candidate.signal_count === "number" && Number.isFinite(candidate.signal_count)
      ? candidate.signal_count
      : signals.length;

  const conviction = String(candidate.conviction ?? "LOW").toUpperCase();
  return (
    {
      id:
        typeof candidate.id === "string"
          ? candidate.id
          : [candidate.asset, timestamp, direction, candidate.regime ?? conviction].join(":"),
      timestamp,
      asset: candidate.asset,
      direction,
      conviction:
        conviction === "HIGH" || conviction === "MEDIUM" ? conviction : "LOW",
      regime:
        typeof candidate.regime === "string" && candidate.regime.length > 0
          ? candidate.regime
          : typeof candidate.type === "string"
            ? candidate.type
            : "Signal",
      signalCount,
      signals,
      price: normalizeNumber(candidate.price),
      vwap: normalizeNumber(candidate.vwap),
      timeframe:
        typeof candidate.timeframe === "string" && candidate.timeframe.length > 0
          ? candidate.timeframe
          : typeof legacyMeta?.timeframe === "string"
            ? legacyMeta.timeframe
            : "hourly",
    }
  );
}

export function parseSignalMessage(data: string | ArrayBuffer | Blob): Signal | null {
  if (typeof data !== "string") {
    return null;
  }

  try {
    const parsed: unknown = JSON.parse(data);
    if (
      parsed &&
      typeof parsed === "object" &&
      "type" in parsed &&
      (parsed as { type?: unknown }).type === "signal" &&
      "data" in parsed
    ) {
      return normalizeSignalRecord((parsed as { data: unknown }).data);
    }

    return normalizeSignalRecord(parsed);
  } catch {
    return null;
  }
}

export function useSignalStream(url?: string): SignalStreamState {
  const streamUrl = useMemo(() => resolveSignalStreamUrl(url), [url]);
  const apiUrl = useMemo(() => resolveApiUrl(), []);
  const [signals, setSignals] = useState<Signal[]>([]);
  const [status, setStatus] = useState<SignalStreamStatus>("connecting");
  const [latestSignalKey, setLatestSignalKey] = useState<string | null>(null);

  useEffect(() => {
    if (typeof window === "undefined") {
      return undefined;
    }

    const controller = new AbortController();

    void fetch(`${apiUrl}/api/signals/history?limit=50`, {
      signal: controller.signal,
    })
      .then(async (response) => {
        if (!response.ok) {
          throw new Error(`signal preload failed: ${response.status}`);
        }
        return response.json();
      })
      .then((payload: unknown) => {
        if (!Array.isArray(payload)) {
          return;
        }
        const normalizedSignals = payload
          .map((entry) => normalizeSignalRecord(entry))
          .filter((entry): entry is Signal => entry !== null);
        setSignals(normalizedSignals.slice(0, SIGNAL_STREAM_MAX_ROWS));
      })
      .catch(() => undefined);

    return () => controller.abort();
  }, [apiUrl]);

  useEffect(() => {
    if (typeof window === "undefined") {
      return undefined;
    }

    let closedByCleanup = false;
    let hasConnected = false;

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
