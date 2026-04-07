"use client";

import { useEffect, useState } from "react";
import {
  SIGNAL_STATUS_META,
  SIGNAL_STREAM_STATUS_EVENT,
  type SignalStreamStatus,
} from "@/lib/ws";

export function BottomTicker() {
  const [status, setStatus] = useState<SignalStreamStatus>("disconnected");
  const statusMeta = SIGNAL_STATUS_META[status];

  useEffect(() => {
    const handleStatus = (event: Event) => {
      const nextStatus = (event as CustomEvent<{ status?: SignalStreamStatus }>).detail
        ?.status;

      if (nextStatus) {
        setStatus(nextStatus);
      }
    };

    window.addEventListener(SIGNAL_STREAM_STATUS_EVENT, handleStatus);

    return () => {
      window.removeEventListener(SIGNAL_STREAM_STATUS_EVENT, handleStatus);
    };
  }, []);

  return (
    <footer className="fixed inset-x-0 bottom-0 z-50 h-7 border-t border-[--border] bg-[--bg-panel-alt]/95 backdrop-blur">
      <div className="mx-auto flex h-full max-w-[1600px] items-center gap-4 overflow-x-auto whitespace-nowrap px-4 font-mono text-[11px] uppercase tracking-[0.18em] text-[--text-secondary] sm:px-6">
        <span className={statusMeta.toneClassName}>● {statusMeta.label}</span>
        <span className="text-[--text-primary]">₿ $69,005</span>
        <span className="text-[--red]">▼</span>
        <span className="text-[--text-primary]">Ξ $2,122</span>
        <span className="text-[--red]">▼</span>
      </div>
    </footer>
  );
}

export default BottomTicker;
