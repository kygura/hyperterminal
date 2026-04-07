"use client";

import { useEffect, useState } from "react";

import { fmt } from "@/lib/margin-engine";
import { AccountState } from "@/lib/types";

const joinClasses = (...values: Array<string | false | null | undefined>): string =>
  values.filter(Boolean).join(" ");

export type FundAction = "add" | "withdraw";

export interface FundDialogSubmit {
  action: FundAction;
  amount: number;
}

export interface FundDialogProps {
  accountState: Pick<AccountState, "availableMargin" | "maxWithdraw" | "totalEquity">;
  className?: string;
  defaultAction?: FundAction;
  onCancel?: () => void;
  onSubmit?: (payload: FundDialogSubmit) => void;
}

export default function FundDialog({
  accountState,
  className,
  defaultAction = "add",
  onCancel,
  onSubmit,
}: FundDialogProps) {
  const [action, setAction] = useState<FundAction>(defaultAction);
  const [amount, setAmount] = useState<number>(0);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setAction(defaultAction);
  }, [defaultAction]);

  const handleSubmit = (): void => {
    if (amount <= 0) {
      setError("Amount must be greater than zero.");
      return;
    }

    if (action === "withdraw" && amount > accountState.maxWithdraw) {
      setError(`Maximum withdraw is $${fmt(accountState.maxWithdraw)}.`);
      return;
    }

    setError(null);
    onSubmit?.({
      action,
      amount,
    });
  };

  return (
    <section className={joinClasses("panel p-4", className)}>
      <div className="border-b border-[--border] pb-4">
        <p className="section-header">Funds</p>
        <p className="mono-data mt-2 text-xs text-[--text-secondary]">
          Add capital or withdraw available cash from the selected branch.
        </p>
      </div>

      <div className="mt-4 grid grid-cols-2 border border-[--border]">
        {([
          { key: "add", label: "Add" },
          { key: "withdraw", label: "Withdraw" },
        ] as const).map((option) => (
          <button
            key={option.key}
            type="button"
            onClick={() => setAction(option.key)}
            className={joinClasses(
              "ui-label px-3 py-3",
              action === option.key
                ? "bg-[--bg-elevated] text-[--text-primary]"
                : "bg-[--bg-body] text-[--text-secondary]",
            )}
          >
            {option.label}
          </button>
        ))}
      </div>

      <div className="mt-4 grid gap-3 md:grid-cols-2">
        <label className="space-y-2">
          <span className="ui-label">Amount</span>
          <input
            className="w-full px-3 py-2.5"
            min={0}
            onChange={(event) => setAmount(Number(event.target.value))}
            step="100"
            type="number"
            value={amount}
          />
        </label>

        <div className="space-y-2">
          <span className="ui-label">Limits</span>
          <div className="border border-[--border] bg-[--bg-panel-alt] px-3 py-2.5">
            <p className="mono-data text-sm text-[--text-primary]">
              {action === "withdraw"
                ? `Max withdraw $${fmt(accountState.maxWithdraw)}`
                : `Available margin $${fmt(accountState.availableMargin)}`}
            </p>
            <p className="mono-data mt-2 text-xs text-[--text-secondary]">
              Total equity ${fmt(accountState.totalEquity)}
            </p>
          </div>
        </div>
      </div>

      {action === "withdraw" ? (
        <div className="mt-3">
          <button
            type="button"
            onClick={() => setAmount(accountState.maxWithdraw)}
            className="ui-label border border-[--border] px-3 py-2.5 text-[--text-secondary]"
          >
            Use max
          </button>
        </div>
      ) : null}

      <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
        {error ? (
          <p className="mono-data text-xs text-[--red]">{error}</p>
        ) : (
          <p className="mono-data text-xs text-[--text-secondary]">
            Withdrawals are capped by realized cash and available margin.
          </p>
        )}

        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={onCancel}
            className="ui-label border border-[--border] px-4 py-3 text-[--text-secondary]"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={handleSubmit}
            className="ui-label border px-4 py-3 text-[--text-primary]"
            style={{
              backgroundColor: action === "add" ? "var(--green)" : "var(--red)",
              borderColor: action === "add" ? "var(--green)" : "var(--red)",
            }}
          >
            {action === "add" ? "Add funds" : "Withdraw funds"}
          </button>
        </div>
      </div>
    </section>
  );
}
