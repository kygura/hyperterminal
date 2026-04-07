"use client";

import type { BranchMetrics } from "@/lib/types";
import { fmt, fmtDate, fmtP } from "@/lib/margin-engine";

export interface BranchSidebarProps {
  branches: BranchMetrics[];
  selectedBranchId?: string;
  onSelectBranch?: (branchId: string) => void;
  onFork?: () => void;
  onImport?: () => void;
  className?: string;
}

const cn = (...values: Array<string | false | null | undefined>): string =>
  values.filter(Boolean).join(" ");

export function BranchSidebar({
  branches,
  selectedBranchId,
  onSelectBranch,
  onFork,
  onImport,
  className,
}: BranchSidebarProps) {
  return (
    <aside
      className={cn(
        "panel flex h-full min-h-[540px] w-full max-w-[230px] flex-col overflow-hidden",
        className,
      )}
    >
      <div className="border-b border-[--border-subtle] bg-[--bg-panel-alt] px-4 py-3">
        <p className="section-header">Branches</p>
      </div>

      <div className="flex-1 space-y-2 overflow-y-auto p-2">
        {branches.length === 0 ? (
          <div className="px-2 py-4">
            <p className="mono-data text-sm text-[--text-secondary]">
              No branches loaded.
            </p>
          </div>
        ) : (
          branches.map((metric) => {
            const isSelected = metric.branch.id === selectedBranchId;

            return (
              <button
                key={metric.branch.id}
                type="button"
                onClick={() => onSelectBranch?.(metric.branch.id)}
                className={cn(
                  "w-full border border-[--border-subtle] p-3 text-left transition-colors",
                  isSelected
                    ? "bg-[--bg-elevated]"
                    : "bg-[--bg-panel] hover:bg-[--bg-hover]",
                )}
              >
                <div className="flex items-start gap-3">
                  <span
                    className="mt-0.5 h-9 w-1 shrink-0"
                    style={{ backgroundColor: metric.branch.color }}
                  />

                  <div className="min-w-0 flex-1">
                    <div className="flex items-center justify-between gap-3">
                      <div className="min-w-0">
                        <div className="flex items-center gap-2">
                          <span
                            className="h-2.5 w-2.5 rounded-full"
                            style={{ backgroundColor: metric.branch.color }}
                          />
                          <span className="truncate ui-label text-[--text-primary]">
                            {metric.branch.name}
                          </span>
                        </div>
                        <p className="mono-data mt-2 text-[11px] text-[--text-secondary]">
                          {metric.branch.isMain ? "MAIN ROOT" : `FORK ${fmtDate(metric.branch.forkDate)}`}
                        </p>
                      </div>

                      <span
                        className={cn(
                          "mono-data text-sm",
                          metric.ret >= 0 ? "text-[--green]" : "text-[--red]",
                        )}
                      >
                        {fmtP(metric.ret)}
                      </span>
                    </div>

                    <div className="mt-3 grid grid-cols-3 gap-2 border-t border-[--border-subtle] pt-3">
                      <div>
                        <p className="ui-label text-[10px]">Value</p>
                        <p className="mono-data mt-1 text-[11px] text-[--text-primary]">
                          ${fmt(metric.val)}
                        </p>
                      </div>
                      <div>
                        <p className="ui-label text-[10px]">MDD</p>
                        <p className="mono-data mt-1 text-[11px] text-[--red]">
                          {fmtP(-metric.mdd)}
                        </p>
                      </div>
                      <div>
                        <p className="ui-label text-[10px]">Pos</p>
                        <p className="mono-data mt-1 text-[11px] text-[--text-primary]">
                          {metric.branch.positions.length}
                        </p>
                      </div>
                    </div>
                  </div>
                </div>
              </button>
            );
          })
        )}
      </div>

      <div className="grid gap-px border-t border-[--border] bg-[--border]">
        <button
          type="button"
          onClick={onFork}
          className="ui-label bg-[--bg-panel] px-4 py-3 text-left text-[--text-primary] transition-colors hover:bg-[--bg-hover]"
        >
          + Fork Branch
        </button>
        <button
          type="button"
          onClick={onImport}
          className="ui-label bg-[--bg-panel] px-4 py-3 text-left text-[--text-primary] transition-colors hover:bg-[--bg-hover]"
        >
          + Import Portfolio
        </button>
      </div>
    </aside>
  );
}

export default BranchSidebar;
