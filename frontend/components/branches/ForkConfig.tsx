"use client";

import { useEffect, useMemo, useState } from "react";

import { accountStateAt, fmt } from "@/lib/margin-engine";
import { OHLC_DATES } from "@/lib/price-data";
import { Branch } from "@/lib/types";

const DEFAULT_FORK_DATE = OHLC_DATES[OHLC_DATES.length - 1] ?? OHLC_DATES[0] ?? "";

const joinClasses = (...values: Array<string | false | null | undefined>): string =>
  values.filter(Boolean).join(" ");

export interface ForkConfigSubmit {
  balance: number;
  forkDate: string;
  name: string;
}

export interface ForkConfigProps {
  className?: string;
  defaultDate?: string;
  defaultName?: string;
  onCancel?: () => void;
  onCreate?: (payload: ForkConfigSubmit) => void;
  parentBranch: Branch;
}

export default function ForkConfig({
  className,
  defaultDate,
  defaultName,
  onCancel,
  onCreate,
  parentBranch,
}: ForkConfigProps) {
  const [name, setName] = useState(defaultName ?? `${parentBranch.name} Fork`);
  const [forkDate, setForkDate] = useState(defaultDate ?? DEFAULT_FORK_DATE);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setName(defaultName ?? `${parentBranch.name} Fork`);
  }, [defaultName, parentBranch.name]);

  useEffect(() => {
    setForkDate(defaultDate ?? DEFAULT_FORK_DATE);
  }, [defaultDate]);

  const accountState = useMemo(
    () => accountStateAt(parentBranch, forkDate),
    [forkDate, parentBranch],
  );

  const handleCreate = (): void => {
    if (!name.trim()) {
      setError("Fork name is required.");
      return;
    }

    if (!forkDate) {
      setError("Fork date is required.");
      return;
    }

    setError(null);
    onCreate?.({
      name: name.trim(),
      forkDate,
      balance: accountState.totalEquity,
    });
  };

  return (
    <section className={joinClasses("panel p-4", className)}>
      <div className="border-b border-[--border] pb-4">
        <p className="section-header">Fork configuration</p>
        <p className="mono-data mt-2 text-xs text-[--text-secondary]">
          Snapshot {parentBranch.name} into a new branch at the selected date.
        </p>
      </div>

      <div className="mt-4 grid gap-3 md:grid-cols-2">
        <label className="space-y-2">
          <span className="ui-label">Name</span>
          <input
            className="w-full px-3 py-2.5"
            onChange={(event) => setName(event.target.value)}
            type="text"
            value={name}
          />
        </label>

        <label className="space-y-2">
          <span className="ui-label">Fork date</span>
          <input
            className="w-full px-3 py-2.5"
            max={OHLC_DATES[OHLC_DATES.length - 1]}
            min={OHLC_DATES[0]}
            onChange={(event) => setForkDate(event.target.value)}
            type="date"
            value={forkDate}
          />
        </label>
      </div>

      <div className="mt-4 grid gap-3 border border-[--border] bg-[--bg-panel-alt] p-4 md:grid-cols-3">
        <div>
          <p className="ui-label">Balance</p>
          <p className="mono-data mt-2 text-sm text-[--text-primary]">
            ${fmt(accountState.totalEquity)}
          </p>
        </div>
        <div>
          <p className="ui-label">Available margin</p>
          <p className="mono-data mt-2 text-sm text-[--text-primary]">
            ${fmt(accountState.availableMargin)}
          </p>
        </div>
        <div>
          <p className="ui-label">Cross equity</p>
          <p className="mono-data mt-2 text-sm text-[--text-primary]">
            ${fmt(accountState.crossEquity)}
          </p>
        </div>
      </div>

      <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
        {error ? (
          <p className="mono-data text-xs text-[--red]">{error}</p>
        ) : (
          <p className="mono-data text-xs text-[--text-secondary]">
            Forks inherit equity at {forkDate || "—"} and start with the same open
            positions.
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
            onClick={handleCreate}
            className="ui-label border border-[--red-accent] bg-[--red-accent] px-4 py-3 text-[--text-primary]"
          >
            Create fork
          </button>
        </div>
      </div>
    </section>
  );
}
