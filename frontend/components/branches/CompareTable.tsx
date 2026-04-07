"use client";

import type { BranchMetrics } from "@/lib/types";
import { fmt, fmtDate, fmtP } from "@/lib/margin-engine";

export interface CompareTableProps {
  branches: BranchMetrics[];
  selectedBranchId?: string;
  onSelectBranch?: (branchId: string) => void;
  className?: string;
}

const cn = (...values: Array<string | false | null | undefined>): string =>
  values.filter(Boolean).join(" ");

const columns = [
  "Branch",
  "Type",
  "Fork",
  "Positions",
  "Return",
  "MDD",
  "Sharpe",
  "Value",
] as const;

export function CompareTable({
  branches,
  selectedBranchId,
  onSelectBranch,
  className,
}: CompareTableProps) {
  return (
    <section className={cn("panel overflow-hidden", className)}>
      <div className="grid grid-cols-[1.6fr_0.8fr_1fr_0.8fr_0.9fr_0.9fr_0.8fr_1fr] gap-3 border-b border-[--border] bg-[--bg-panel-alt] px-4 py-3">
        {columns.map((column) => (
          <span key={column} className="ui-label">
            {column}
          </span>
        ))}
      </div>

      {branches.length === 0 ? (
        <div className="px-4 py-8">
          <p className="mono-data text-sm text-[--text-secondary]">
            No branches available for comparison.
          </p>
        </div>
      ) : (
        <div className="divide-y divide-[--border-subtle]">
          {branches.map((metric) => {
            const isSelected = metric.branch.id === selectedBranchId;

            return (
              <button
                key={metric.branch.id}
                type="button"
                onClick={() => onSelectBranch?.(metric.branch.id)}
                className={cn(
                  "grid w-full grid-cols-[1.6fr_0.8fr_1fr_0.8fr_0.9fr_0.9fr_0.8fr_1fr] gap-3 px-4 py-3 text-left transition-colors",
                  isSelected
                    ? "bg-[--bg-elevated]"
                    : "bg-[--bg-panel] hover:bg-[--bg-hover]",
                )}
              >
                <span className="flex items-center gap-3">
                  <span
                    className="h-2.5 w-2.5 rounded-full"
                    style={{ backgroundColor: metric.branch.color }}
                  />
                  <span className="mono-data text-sm text-[--text-primary]">
                    {metric.branch.name}
                  </span>
                </span>
                <span className="ui-label text-[--text-muted]">
                  {metric.branch.isMain ? "Main" : "Fork"}
                </span>
                <span className="mono-data text-sm text-[--text-secondary]">
                  {fmtDate(metric.branch.forkDate)}
                </span>
                <span className="mono-data text-sm text-[--text-primary]">
                  {metric.branch.positions.length}
                </span>
                <span
                  className={cn(
                    "mono-data text-sm",
                    metric.ret >= 0 ? "text-[--green]" : "text-[--red]",
                  )}
                >
                  {fmtP(metric.ret)}
                </span>
                <span className="mono-data text-sm text-[--red]">
                  {fmtP(-metric.mdd)}
                </span>
                <span className="mono-data text-sm text-[--text-primary]">
                  {fmt(metric.sharpe)}
                </span>
                <span className="mono-data text-sm text-[--text-primary]">
                  ${fmt(metric.val)}
                </span>
              </button>
            );
          })}
        </div>
      )}
    </section>
  );
}

export default CompareTable;
