"use client";

import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useRef,
} from "react";
import type { QFChartCandle } from "@/lib/charts";

export interface ChartHandle {
  addIndicator: (
    id: string,
    plots: Record<string, unknown>,
    options?: { overlay?: boolean; height?: number },
  ) => void;
  removeIndicator: (id: string) => void;
  setMarketData: (data: QFChartCandle[]) => void;
}

interface Props {
  data: QFChartCandle[];
  title: string;
}

const CandlestickChart = forwardRef<ChartHandle, Props>(
  function CandlestickChart({ data, title }, ref) {
    const containerRef = useRef<HTMLDivElement>(null);
    const chartRef = useRef<InstanceType<typeof import("@qfo/qfchart").QFChart> | null>(null);
    const pluginsRegistered = useRef(false);

    useImperativeHandle(ref, () => ({
      addIndicator(id, plots, options) {
        chartRef.current?.addIndicator(
          id,
          plots as Record<string, never>,
          options,
        );
      },
      removeIndicator(id) {
        chartRef.current?.removeIndicator(id);
      },
      setMarketData(nextData) {
        chartRef.current?.setMarketData(nextData);
      },
    }));

    useEffect(() => {
      let chart: InstanceType<typeof import("@qfo/qfchart").QFChart> | null = null;

      const init = async () => {
        if (!containerRef.current) return;

        const { QFChart, LineTool, FibonacciTool, MeasureTool } = await import(
          "@qfo/qfchart"
        );

        chart = new QFChart(containerRef.current, {
          title,
          height: "100%",
          backgroundColor: "#111111",
          upColor: "#38a67c",
          downColor: "#bc263e",
          fontColor: "#928d86",
          fontFamily: "var(--font-jetbrains-mono), monospace",
          watermark: false,
          padding: 0.15,
          dataZoom: {
            visible: true,
            position: "bottom",
            height: 4,
            start: 60,
            end: 100,
          },
          databox: { position: "right" },
          layout: { mainPaneHeight: "70%", gap: 8 },
          controls: { collapse: true, maximize: true, fullscreen: true },
        });

        chart.setMarketData(data);

        if (!pluginsRegistered.current) {
          chart.registerPlugin(new LineTool());
          chart.registerPlugin(new FibonacciTool());
          chart.registerPlugin(new MeasureTool());
          pluginsRegistered.current = true;
        }

        chartRef.current = chart;
      };

      void init();

      return () => {
        chart?.destroy();
        chartRef.current = null;
        pluginsRegistered.current = false;
      };
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [title]);

    useEffect(() => {
      if (chartRef.current && data.length > 0) {
        chartRef.current.setMarketData(data);
      }
    }, [data]);

    return (
      <div
        ref={containerRef}
        className="h-[600px] w-full"
      />
    );
  },
);

export default CandlestickChart;
