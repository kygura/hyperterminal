import type { AccountState, BranchMetrics } from "@/lib/types";
import { fmt, fmtDate, fmtP } from "@/lib/margin-engine";

export interface MetricsPanelProps {
  metrics: BranchMetrics;
  account?: AccountState;
  className?: string;
}

const cn = (...values: Array<string | false | null | undefined>): string =>
  values.filter(Boolean).join(" ");

const money = (value: number): string => `$${fmt(value)}`;

export function MetricsPanel({
  metrics,
  account,
  className,
}: MetricsPanelProps) {
  const { branch } = metrics;
  const openPositions = branch.positions.filter((position) => !position.exitDate).length;
  const closedPositions = branch.positions.length - openPositions;

  const overview = [
    { label: "Branch", value: branch.name },
    { label: "Fork Date", value: fmtDate(branch.forkDate) },
    { label: "Parent", value: branch.parentId ?? "ROOT" },
    { label: "Base Balance", value: money(branch.balance) },
    { label: "Open Positions", value: String(openPositions) },
    { label: "Closed Positions", value: String(closedPositions) },
  ];

  const analysis = [
    { label: "Return", value: fmtP(metrics.ret) },
    { label: "Max Drawdown", value: fmtP(-metrics.mdd) },
    { label: "Sharpe", value: fmt(metrics.sharpe) },
    { label: "Terminal Equity", value: money(metrics.val) },
    {
      label: "Account Equity",
      value: account ? money(account.totalEquity) : "—",
    },
    {
      label: "Available Margin",
      value: account ? money(account.availableMargin) : "—",
    },
  ];

  return (
    <section className={cn("grid gap-4 xl:grid-cols-2", className)}>
      <div className="panel overflow-hidden">
        <div className="border-b border-[--border-subtle] bg-[--bg-panel-alt] px-4 py-3">
          <p className="section-header">Overview</p>
        </div>
        <div className="divide-y divide-[--border-subtle]">
          {overview.map((item) => (
            <div
              key={item.label}
              className="flex items-center justify-between gap-4 px-4 py-3"
            >
              <span className="ui-label">{item.label}</span>
              <span className="mono-data text-sm text-[--text-primary]">
                {item.value}
              </span>
            </div>
          ))}
        </div>
      </div>

      <div className="panel overflow-hidden">
        <div className="border-b border-[--border-subtle] bg-[--bg-panel-alt] px-4 py-3">
          <p className="section-header">Analysis</p>
        </div>
        <div className="divide-y divide-[--border-subtle]">
          {analysis.map((item) => (
            <div
              key={item.label}
              className="flex items-center justify-between gap-4 px-4 py-3"
            >
              <span className="ui-label">{item.label}</span>
              <span
                className={cn(
                  "mono-data text-sm",
                  item.label === "Return"
                    ? metrics.ret >= 0
                      ? "text-[--green]"
                      : "text-[--red]"
                    : item.label === "Max Drawdown"
                      ? "text-[--red]"
                      : "text-[--text-primary]",
                )}
              >
                {item.value}
              </span>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

export default MetricsPanel;
