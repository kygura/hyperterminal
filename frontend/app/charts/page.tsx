"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import CandlestickChart, {
  type ChartHandle,
} from "@/components/charts/CandlestickChart";
import IndicatorEditor from "@/components/charts/IndicatorEditor";
import IndicatorList from "@/components/charts/IndicatorList";
import { candlesToQFChart } from "@/lib/charts";
import {
  candlesForAsset,
  setOhlcDataset,
  type OhlcDataset,
} from "@/lib/price-data";
import { ASSETS } from "@/lib/types";
import { resolveApiUrl } from "@/lib/ws";

const CHART_ASSETS = ["BTC", "ETH", "HYPE"] as const;
type ChartAsset = (typeof CHART_ASSETS)[number];

interface ActiveIndicator {
  id: string;
  name: string;
}

export default function ChartsPage() {
  const [asset, setAsset] = useState<ChartAsset>("BTC");
  const [activeIndicators, setActiveIndicators] = useState<ActiveIndicator[]>([]);
  const [code, setCode] = useState("");
  const [, setPriceVersion] = useState(0);
  const chartRef = useRef<ChartHandle>(null);

  useEffect(() => {
    const controller = new AbortController();

    void fetch(`${resolveApiUrl()}/api/branches/price-history`, {
      signal: controller.signal,
    })
      .then(async (res) => {
        if (!res.ok) throw new Error(`price history: ${res.status}`);
        return res.json();
      })
      .then((payload: OhlcDataset) => {
        if (!payload?.assets) return;
        setOhlcDataset(payload);
        setPriceVersion((v) => v + 1);
      })
      .catch(() => undefined);

    return () => controller.abort();
  }, []);

  const candles = useMemo(() => candlesForAsset(asset), [asset]);
  const chartData = useMemo(() => candlesToQFChart(candles), [candles]);

  const handleAssetChange = useCallback((next: ChartAsset) => {
    setAsset(next);
    setActiveIndicators([]);
  }, []);

  const handleIndicatorResult = useCallback(
    (id: string, plots: Record<string, unknown>, overlay: boolean) => {
      chartRef.current?.addIndicator(id, plots, { overlay });
      setActiveIndicators((prev) => [
        ...prev,
        { id, name: id.replace("pine_", "Indicator ") },
      ]);
    },
    [],
  );

  const handleRemoveIndicator = useCallback((id: string) => {
    chartRef.current?.removeIndicator(id);
    setActiveIndicators((prev) => prev.filter((i) => i.id !== id));
  }, []);

  const handleLoadCode = useCallback((loadedCode: string) => {
    setCode(loadedCode);
  }, []);

  const assetConfig = ASSETS[asset];

  return (
    <section className="space-y-5">
      <header className="flex flex-wrap items-end justify-between gap-4 border-b border-[--border-subtle] pb-4">
        <div className="space-y-2">
          <p className="section-header">Charts</p>
          <h1 className="text-2xl font-medium uppercase tracking-[0.08em] text-[--text-primary]">
            Asset charts
          </h1>
        </div>
        <p className="mono-data text-xs text-[--text-secondary]">
          {assetConfig?.name ?? asset} · {candles.length} candles
        </p>
      </header>

      <div className="flex gap-2">
        {CHART_ASSETS.map((a) => {
          const active = a === asset;
          return (
            <button
              key={a}
              type="button"
              onClick={() => handleAssetChange(a)}
              className={[
                "ui-label border px-4 py-2 transition-colors",
                active
                  ? "border-[--red-accent] bg-[--red-accent]/10 text-[--red-accent]"
                  : "border-[--border] text-[--text-secondary] hover:bg-[--bg-hover] hover:text-[--text-primary]",
              ].join(" ")}
            >
              {a}
            </button>
          );
        })}
      </div>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_320px]">
        <div className="panel overflow-hidden">
          <CandlestickChart
            ref={chartRef}
            data={chartData}
            title={assetConfig?.name ?? `${asset}-USD`}
          />
        </div>

        <aside className="space-y-4">
          <div className="panel p-4">
            <IndicatorEditor
              candles={candles}
              onResult={handleIndicatorResult}
              code={code}
              onCodeChange={setCode}
            />
          </div>

          <div className="panel p-4">
            <IndicatorList
              activeIndicators={activeIndicators}
              onLoad={handleLoadCode}
              onRemove={handleRemoveIndicator}
            />
          </div>
        </aside>
      </div>
    </section>
  );
}
