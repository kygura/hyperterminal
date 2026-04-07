"use client";

import { useCallback, useState } from "react";
import { runIndicator, saveIndicator } from "@/lib/charts";
import type { Candle } from "@/lib/price-data";

const PLACEHOLDER = `//@version=5
indicator("My Indicator")
plot(ta.sma(close, 20), "SMA 20", color.blue)`;

interface Props {
  candles: Candle[];
  onResult: (
    id: string,
    plots: Record<string, unknown>,
    overlay: boolean,
  ) => void;
  code: string;
  onCodeChange: (code: string) => void;
}

export default function IndicatorEditor({
  candles,
  onResult,
  code,
  onCodeChange,
}: Props) {
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [saveName, setSaveName] = useState("");

  const handleRun = useCallback(async () => {
    if (!code.trim() || candles.length === 0) return;
    setRunning(true);
    setError(null);

    const { plots, error: err } = await runIndicator(code, candles);

    if (err) {
      setError(err);
    } else if (plots) {
      const isOverlay = code.includes("overlay=true") || code.includes("overlay = true");
      const id = `pine_${Date.now()}`;
      onResult(id, plots, isOverlay);
    }

    setRunning(false);
  }, [code, candles, onResult]);

  const handleSave = useCallback(() => {
    const name = saveName.trim();
    if (!name || !code.trim()) return;
    saveIndicator(name, code);
    setSaveName("");
  }, [saveName, code]);

  return (
    <div className="space-y-3">
      <p className="section-header">Pine Script Editor</p>

      <textarea
        className="mono-data h-52 w-full resize-y rounded-sm border border-[--border] bg-[--bg-body] p-3 text-xs leading-5 text-[--text-primary] placeholder:text-[--text-secondary] focus:border-[--red-accent] focus:outline-none"
        spellCheck={false}
        placeholder={PLACEHOLDER}
        value={code}
        onChange={(e) => onCodeChange(e.target.value)}
      />

      <div className="flex items-center gap-2">
        <button
          type="button"
          disabled={running || !code.trim()}
          onClick={handleRun}
          className="ui-label border border-[--red-accent] bg-[--red-accent]/10 px-4 py-2 text-[--red-accent] transition-colors hover:bg-[--red-accent]/20 disabled:opacity-40"
        >
          {running ? "Running..." : "Run"}
        </button>

        <input
          type="text"
          placeholder="Indicator name"
          value={saveName}
          onChange={(e) => setSaveName(e.target.value)}
          className="flex-1 px-3 py-2 text-xs"
        />

        <button
          type="button"
          disabled={!saveName.trim() || !code.trim()}
          onClick={handleSave}
          className="ui-label border border-[--border] px-4 py-2 text-[--text-primary] transition-colors hover:bg-[--bg-hover] disabled:opacity-40"
        >
          Save
        </button>
      </div>

      {error && (
        <div className="mono-data border border-[--red]/30 bg-[--red]/5 p-3 text-xs text-[--red]">
          {error}
        </div>
      )}
    </div>
  );
}
