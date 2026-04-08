"use client";

import { useCallback, useEffect, useMemo, useRef, useState, Suspense } from "react";
import { useSearchParams } from "next/navigation";

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

function ChartsContent() {
  const searchParams = useSearchParams();
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

  useEffect(() => {
    const qAsset = searchParams.get("asset") as ChartAsset | null;
    const qTime = searchParams.get("time");
    const qDir = searchParams.get("dir");
    const qPrice = searchParams.get("price");

    if (qAsset && CHART_ASSETS.includes(qAsset) && qAsset !== asset) {
      setAsset(qAsset);
    }

    if (qTime && qDir && chartRef.current && chartData.length > 0) {
      const timeMs = parseInt(qTime, 10);
      const isLong = qDir === "Long";
      
      let plotValue = 0;
      if (qPrice && !isNaN(parseFloat(qPrice))) {
        plotValue = parseFloat(qPrice);
      } else {
        // Find closest candle if no price
        const candle = chartData.find(c => c.time >= timeMs) || chartData[chartData.length - 1];
        plotValue = candle ? candle.close : 0;
      }

      if (plotValue === 0) return;

      const plots = {
        signal: {
          data: [{
            time: timeMs,
            value: plotValue
          }],
          options: {
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            style: "shape" as any,
            shape: isLong ? "arrow_up" : "arrow_down",
            color: isLong ? "#38a67c" : "#bc263e",
            location: isLong ? "belowBar" : "aboveBar",
            size: "large"
          }
        }
      };

      // Add marker after short delay to ensure chart handles data update first
      const t = setTimeout(() => {
        chartRef.current?.addIndicator("signal_marker", plots, { overlay: true });
      }, 50);
      return () => clearTimeout(t);
    }
  }, [searchParams, chartData, asset]);

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

export default function ChartsPage() {
  return (
    <Suspense fallback={<div className="panel p-8 text-center text-[--text-secondary]">Loading charts...</div>}>
      <ChartsContent />
    </Suspense>
  );
}

