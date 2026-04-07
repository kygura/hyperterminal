import type { AccountState } from "@/lib/types";
import { fmt } from "@/lib/margin-engine";

export interface AccountBarProps {
  account: AccountState;
  leverage?: number;
  branchLabel?: string;
  className?: string;
}

const cn = (...values: Array<string | false | null | undefined>): string =>
  values.filter(Boolean).join(" ");

const money = (value: number): string => `${value >= 0 ? "+" : "-"}$${fmt(Math.abs(value))}`;

const neutralMoney = (value: number): string => `$${fmt(value)}`;

export function AccountBar({
  account,
  leverage,
  branchLabel,
  className,
}: AccountBarProps) {
  const unrealizedPnl = account.crossUPnL + account.isoUPnL;
  const marginUsed = account.crossMarginUsed + account.isoMarginUsed;
  const derivedLeverage =
    leverage ?? (account.totalEquity > 0 ? marginUsed / account.totalEquity : 0);

  const stats = [
    {
      label: "Account Value",
      value: neutralMoney(account.totalEquity),
      tone: "text-[--text-primary]",
    },
    {
      label: "Unrealized PnL",
      value: money(unrealizedPnl),
      tone: unrealizedPnl >= 0 ? "text-[--green]" : "text-[--red]",
    },
    {
      label: "Margin Used",
      value: neutralMoney(marginUsed),
      tone: "text-[--text-primary]",
    },
    {
      label: "Available",
      value: neutralMoney(account.availableMargin),
      tone: "text-[--text-primary]",
    },
    {
      label: "Leverage",
      value: `${fmt(derivedLeverage, 2)}×`,
      tone: "text-[--text-primary]",
    },
  ];

  return (
    <section className={cn("panel overflow-hidden", className)}>
      <div className="flex items-center justify-between border-b border-[--border-subtle] bg-[--bg-panel-alt] px-4 py-3">
        <p className="section-header">Account</p>
        {branchLabel ? (
          <p className="mono-data text-xs uppercase tracking-[0.12em] text-[--text-secondary]">
            {branchLabel}
          </p>
        ) : null}
      </div>

      <div className="grid gap-px bg-[--border] sm:grid-cols-2 xl:grid-cols-5">
        {stats.map((stat) => (
          <div
            key={stat.label}
            className="bg-[--bg-panel] px-4 py-3.5"
          >
            <p className="ui-label">{stat.label}</p>
            <p className={cn("mono-data mt-2 text-sm", stat.tone)}>{stat.value}</p>
          </div>
        ))}
      </div>
    </section>
  );
}

export default AccountBar;
