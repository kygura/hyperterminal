"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import type { BranchMetrics } from "@/lib/types";
import { fmt, fmtDate, fmtP } from "@/lib/margin-engine";

export interface EquityChartProps {
  branches: BranchMetrics[];
  selectedBranchId?: string;
  onSelectBranch?: (branchId: string) => void;
  height?: number;
  className?: string;
}

interface ChartSize {
  width: number;
  height: number;
}

const cn = (...values: Array<string | false | null | undefined>): string =>
  values.filter(Boolean).join(" ");

const GRID_COLOR = "rgba(255,255,255,0.07)";
const AXIS_COLOR = "rgba(255,255,255,0.42)";

export function EquityChart({
  branches,
  selectedBranchId,
  onSelectBranch,
  height = 320,
  className,
}: EquityChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const [size, setSize] = useState<ChartSize>({ width: 0, height });

  const selectedId = selectedBranchId ?? branches[0]?.branch.id;

  const plotData = useMemo(() => {
    const maxPoints = Math.max(...branches.map((metric) => metric.eq.length), 0);
    const values = branches.flatMap((metric) =>
      metric.eq.filter((value) => Number.isFinite(value)),
    );

    if (values.length === 0 || maxPoints === 0) {
      return null;
    }

    const minValue = Math.min(...values);
    const maxValue = Math.max(...values);
    const range = Math.max(maxValue - minValue, Math.max(maxValue * 0.04, 1));

    return {
      minValue,
      maxValue,
      range,
      maxPoints,
      startLabel: branches[0]?.branch.forkDate ?? "",
      endLabel:
        branches
          .map((metric) => metric.branch.positions.at(-1)?.exitDate)
          .find(Boolean) ?? "",
    };
  }, [branches]);

  useEffect(() => {
    const element = containerRef.current;

    if (!element) {
      return;
    }

    const update = () => {
      const rect = element.getBoundingClientRect();
      setSize({ width: rect.width, height });
    };

    update();

    const observer = new ResizeObserver(update);
    observer.observe(element);

    return () => observer.disconnect();
  }, [height]);

  useEffect(() => {
    const canvas = canvasRef.current;

    if (!canvas || !plotData || size.width === 0) {
      return;
    }

    const ctx = canvas.getContext("2d");

    if (!ctx) {
      return;
    }

    const dpr = window.devicePixelRatio || 1;
    const width = size.width;
    const drawHeight = size.height;
    const padding = { top: 18, right: 64, bottom: 26, left: 14 };
    const plotWidth = Math.max(1, width - padding.left - padding.right);
    const plotHeight = Math.max(1, drawHeight - padding.top - padding.bottom);

    canvas.width = Math.floor(width * dpr);
    canvas.height = Math.floor(drawHeight * dpr);
    canvas.style.width = `${width}px`;
    canvas.style.height = `${drawHeight}px`;

    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, drawHeight);

    const xFor = (index: number) =>
      padding.left +
      (index / Math.max(plotData.maxPoints - 1, 1)) * plotWidth;
    const yFor = (value: number) =>
      padding.top +
      (1 - (value - plotData.minValue) / plotData.range) * plotHeight;

    ctx.strokeStyle = GRID_COLOR;
    ctx.lineWidth = 1;

    for (let lineIndex = 0; lineIndex <= 4; lineIndex += 1) {
      const y = padding.top + (plotHeight / 4) * lineIndex;
      ctx.beginPath();
      ctx.moveTo(padding.left, y);
      ctx.lineTo(width - padding.right, y);
      ctx.stroke();
    }

    for (let lineIndex = 0; lineIndex <= 5; lineIndex += 1) {
      const x = padding.left + (plotWidth / 5) * lineIndex;
      ctx.beginPath();
      ctx.moveTo(x, padding.top);
      ctx.lineTo(x, drawHeight - padding.bottom);
      ctx.stroke();
    }

    branches.forEach((metric) => {
      const isSelected = metric.branch.id === selectedId;
      const alpha = isSelected ? 1 : 0.3;
      let started = false;

      ctx.beginPath();
      ctx.lineWidth = isSelected ? 2.5 : 1.3;
      ctx.strokeStyle = withAlpha(metric.branch.color, alpha);

      metric.eq.forEach((value, index) => {
        if (!Number.isFinite(value)) {
          return;
        }

        const x = xFor(index);
        const y = yFor(value);

        if (!started) {
          ctx.moveTo(x, y);
          started = true;
        } else {
          ctx.lineTo(x, y);
        }
      });

      ctx.stroke();

      const forkValue = metric.eq[metric.forkIdx];
      if (Number.isFinite(forkValue)) {
        ctx.fillStyle = metric.branch.color;
        ctx.beginPath();
        ctx.arc(xFor(metric.forkIdx), yFor(forkValue), isSelected ? 4 : 3, 0, Math.PI * 2);
        ctx.fill();
      }
    });

    ctx.fillStyle = AXIS_COLOR;
    ctx.font = "11px var(--font-jetbrains-mono), monospace";
    ctx.textAlign = "right";

    for (let lineIndex = 0; lineIndex <= 4; lineIndex += 1) {
      const value =
        plotData.maxValue - (plotData.range / 4) * lineIndex;
      const y = padding.top + (plotHeight / 4) * lineIndex;
      ctx.fillText(`$${fmt(value, 0)}`, width - 8, y + 4);
    }
  }, [branches, plotData, selectedId, size]);

  const latestMetric = branches.find((metric) => metric.branch.id === selectedId) ?? branches[0];

  return (
    <section className={cn("panel overflow-hidden", className)}>
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-[--border-subtle] bg-[--bg-panel-alt] px-4 py-3">
        <div>
          <p className="section-header">Equity Curve</p>
          <p className="mono-data mt-2 text-xs text-[--text-secondary]">
            {latestMetric
              ? `${latestMetric.branch.name} · ${fmtP(latestMetric.ret)}`
              : "Awaiting branch data"}
          </p>
        </div>

        <div className="flex flex-wrap items-center justify-end gap-2">
          {branches.map((metric) => {
            const isSelected = metric.branch.id === selectedId;

            return (
              <button
                key={metric.branch.id}
                type="button"
                onClick={() => onSelectBranch?.(metric.branch.id)}
                className={cn(
                  "flex items-center gap-2 border px-2.5 py-1.5 transition-colors",
                  isSelected
                    ? "border-[--border] bg-[--bg-elevated]"
                    : "border-[--border-subtle] bg-[--bg-panel] hover:bg-[--bg-hover]",
                )}
              >
                <span
                  className="h-2 w-2 rounded-full"
                  style={{ backgroundColor: metric.branch.color }}
                />
                <span className="mono-data text-[11px] text-[--text-primary]">
                  {metric.branch.name}
                </span>
              </button>
            );
          })}
        </div>
      </div>

      {branches.length === 0 ? (
        <div className="px-4 py-10">
          <p className="mono-data text-sm text-[--text-secondary]">
            No equity series available.
          </p>
        </div>
      ) : (
        <div className="px-4 py-4">
          <div
            ref={containerRef}
            className="relative w-full overflow-hidden border border-[--border-subtle] bg-[--bg-panel]"
            style={{ height }}
          >
            <canvas ref={canvasRef} />

            <div className="pointer-events-none absolute inset-x-3 bottom-2 flex items-center justify-between">
              <span className="mono-data text-[10px] text-[--text-secondary]">
                {latestMetric ? fmtDate(latestMetric.branch.forkDate) : "—"}
              </span>
              <span className="mono-data text-[10px] text-[--text-secondary]">
                {latestMetric ? `${latestMetric.eq.length} pts` : "—"}
              </span>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}

const withAlpha = (hex: string, alpha: number): string => {
  const normalized = hex.replace("#", "");

  if (normalized.length !== 6) {
    return hex;
  }

  const red = Number.parseInt(normalized.slice(0, 2), 16);
  const green = Number.parseInt(normalized.slice(2, 4), 16);
  const blue = Number.parseInt(normalized.slice(4, 6), 16);

  return `rgba(${red}, ${green}, ${blue}, ${alpha})`;
};

export default EquityChart;
