"use client";

import { SignalFeed } from "@/components/signals/SignalFeed";
import {
  SIGNAL_STATUS_META,
  SIGNAL_STREAM_MAX_ROWS,
  useSignalStream,
} from "@/lib/ws";

export default function SignalsPage() {
  const { signals, status, streamUrl, latestSignalKey } = useSignalStream();
  const statusMeta = SIGNAL_STATUS_META[status];

  return (
    <section className="space-y-5">
      <header className="flex items-end justify-between gap-4 border-b border-[--border-subtle] pb-4">
        <div className="space-y-2">
          <p className="section-header">Signals</p>
          <h1 className="text-2xl font-medium uppercase tracking-[0.08em] text-[--text-primary]">
            Live signal matrix
          </h1>
        </div>

        <div className="space-y-2 text-right">
          <p
            className={`mono-data text-xs uppercase tracking-[0.16em] ${statusMeta.toneClassName}`}
          >
            ● {statusMeta.label}
          </p>
          <p className="mono-data text-[11px] text-[--text-secondary]">
            {signals.length} / {SIGNAL_STREAM_MAX_ROWS} rows
          </p>
        </div>
      </header>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_260px]">
        <SignalFeed
          signals={signals}
          status={status}
          latestSignalKey={latestSignalKey}
        />

        <aside className="panel p-4">
          <div className="space-y-4">
            <div>
              <p className="section-header">Stream</p>
              <p className="mono-data mt-3 text-sm text-[--text-primary]">
                {streamUrl}
              </p>
            </div>

            <div className="border-t border-[--border-subtle] pt-4">
              <p className="section-header">Status Surface</p>
              <div className="mt-3 space-y-3">
                <div className="flex items-center justify-between gap-3">
                  <span className="ui-label">Connection</span>
                  <span className={`mono-data text-xs ${statusMeta.toneClassName}`}>
                    {statusMeta.label}
                  </span>
                </div>
                <div className="flex items-center justify-between gap-3">
                  <span className="ui-label">Visible rows</span>
                  <span className="mono-data text-xs text-[--text-primary]">
                    {signals.length}
                  </span>
                </div>
                <div className="flex items-center justify-between gap-3">
                  <span className="ui-label">Transport</span>
                  <span className="mono-data text-xs text-[--text-primary]">WS</span>
                </div>
              </div>
            </div>

            <div className="border-t border-[--border-subtle] pt-4">
              <p className="section-header">Ticker sync</p>
              <p className="mono-data mt-3 text-xs leading-6 text-[--text-secondary]">
                BottomTicker listens to
                {" "}
                <span className="text-[--text-primary]">frontend/lib/ws.ts</span>
                {" "}
                and mirrors the current websocket status.
              </p>
            </div>
          </div>
        </aside>
      </div>
    </section>
  );
}
