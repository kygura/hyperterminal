"use client";

export type BranchTab = "positions" | "metrics" | "compare";

export interface BranchTabOption {
  id: BranchTab;
  label: string;
  count?: number;
  disabled?: boolean;
}

export interface BranchTabsProps {
  activeTab: BranchTab;
  onChange?: (tab: BranchTab) => void;
  tabs?: BranchTabOption[];
  className?: string;
}

export const DEFAULT_BRANCH_TABS: BranchTabOption[] = [
  { id: "positions", label: "Positions" },
  { id: "metrics", label: "Metrics" },
  { id: "compare", label: "Compare" },
];

const cn = (...values: Array<string | false | null | undefined>): string =>
  values.filter(Boolean).join(" ");

export function BranchTabs({
  activeTab,
  onChange,
  tabs = DEFAULT_BRANCH_TABS,
  className,
}: BranchTabsProps) {
  return (
    <div
      className={cn(
        "flex items-center gap-5 border-b border-[--border] bg-[--bg-panel] px-4",
        className,
      )}
      role="tablist"
      aria-label="Branch views"
    >
      {tabs.map((tab) => {
        const isActive = tab.id === activeTab;

        return (
          <button
            key={tab.id}
            type="button"
            role="tab"
            aria-selected={isActive}
            aria-controls={`branch-tabpanel-${tab.id}`}
            disabled={tab.disabled}
            onClick={() => onChange?.(tab.id)}
            className={cn(
              "ui-label inline-flex min-h-12 items-center gap-2 border-b-2 pb-px pt-1 transition-colors",
              isActive
                ? "border-[--red-accent] text-[--text-primary]"
                : "border-transparent text-[--text-secondary] hover:text-[--text-primary]",
              tab.disabled && "cursor-not-allowed opacity-40",
            )}
          >
            <span>{tab.label}</span>
            {typeof tab.count === "number" ? (
              <span className="mono-data text-[10px] text-[--text-muted]">
                {tab.count}
              </span>
            ) : null}
          </button>
        );
      })}
    </div>
  );
}

export default BranchTabs;
