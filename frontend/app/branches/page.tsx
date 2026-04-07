"use client";

import { useEffect, useMemo, useState } from "react";

import AccountBar from "@/components/branches/AccountBar";
import BranchSidebar from "@/components/branches/BranchSidebar";
import BranchTabs, { type BranchTab } from "@/components/branches/BranchTabs";
import CompareTable from "@/components/branches/CompareTable";
import EquityChart from "@/components/branches/EquityChart";
import ForkConfig, {
  type ForkConfigSubmit,
} from "@/components/branches/ForkConfig";
import FundDialog, {
  type FundDialogSubmit,
} from "@/components/branches/FundDialog";
import ImportDialog from "@/components/branches/ImportDialog";
import MetricsPanel from "@/components/branches/MetricsPanel";
import PositionEntry, {
  type PositionEntrySubmit,
} from "@/components/branches/PositionEntry";
import PositionRow from "@/components/branches/PositionRow";
import {
  accountStateAt,
  computeAllBranches,
  posStateAt,
} from "@/lib/margin-engine";
import { closeAt, OHLC_ASSETS, OHLC_DATES } from "@/lib/price-data";
import type { Branch, Position } from "@/lib/types";

const BRANCH_COLORS = ["#ed3602", "#38a67c", "#627eea", "#f7931a", "#9945ff", "#50d2c1"];
const LATEST_DATE = OHLC_DATES[OHLC_DATES.length - 1] ?? "";

const createId = (prefix: string): string =>
  globalThis.crypto?.randomUUID?.() ??
  `${prefix}_${Math.random().toString(36).slice(2, 10)}`;

const dateFromEnd = (offset: number): string =>
  OHLC_DATES[Math.max(0, OHLC_DATES.length - 1 - offset)] ?? OHLC_DATES[0] ?? "";

const createSeedPosition = (
  config: Omit<Position, "id" | "entryPrice"> & { exitDate?: string },
): Position => ({
  ...config,
  id: createId("pos"),
  entryPrice: closeAt(config.asset, config.entryDate),
  exitPrice: config.exitDate ? closeAt(config.asset, config.exitDate) : undefined,
});

const createInitialBranches = (): Branch[] => [
  {
    id: createId("branch"),
    name: "Main Portfolio",
    color: BRANCH_COLORS[0],
    isMain: true,
    forkDate: dateFromEnd(120),
    balance: 100_000,
    positions: [
      createSeedPosition({
        asset: "BTC",
        direction: "Long",
        mode: "Cross",
        leverage: 8,
        margin: 12_000,
        entryDate: dateFromEnd(95),
      }),
      createSeedPosition({
        asset: "ETH",
        direction: "Short",
        mode: "Isolated",
        leverage: 5,
        margin: 7_500,
        entryDate: dateFromEnd(72),
        exitDate: dateFromEnd(38),
      }),
      createSeedPosition({
        asset: "SOL",
        direction: "Long",
        mode: "Cross",
        leverage: 6,
        margin: 9_000,
        entryDate: dateFromEnd(28),
      }),
    ],
  },
];

const duplicatePosition = (position: Position): Position => ({
  ...position,
  id: createId("pos"),
});

export default function BranchesPage() {
  const [branches, setBranches] = useState<Branch[]>(() => createInitialBranches());
  const [selectedBranchIdx, setSelectedBranchIdx] = useState(0);
  const [activeTab, setActiveTab] = useState<BranchTab>("positions");
  const [showFork, setShowFork] = useState(false);
  const [showImport, setShowImport] = useState(false);
  const [showFund, setShowFund] = useState(false);

  const branchData = useMemo(() => computeAllBranches(branches), [branches]);

  useEffect(() => {
    setSelectedBranchIdx((current) =>
      branches.length === 0 ? 0 : Math.min(current, branches.length - 1),
    );
  }, [branches.length]);

  const selectedBranch = branches[selectedBranchIdx] ?? branches[0] ?? null;
  const selectedMetrics = branchData[selectedBranchIdx] ?? branchData[0] ?? null;
  const evaluationDate = LATEST_DATE || selectedBranch?.forkDate || "";

  const selectedAccount = useMemo(
    () => (selectedBranch ? accountStateAt(selectedBranch, evaluationDate) : null),
    [evaluationDate, selectedBranch],
  );

  const selectedPositionStates = useMemo(
    () =>
      selectedBranch
        ? Object.fromEntries(
            selectedBranch.positions.map((position) => [
              position.id,
              posStateAt(position, evaluationDate),
            ]),
          )
        : {},
    [evaluationDate, selectedBranch],
  );

  const selectBranch = (branchId: string): void => {
    const nextIndex = branches.findIndex((branch) => branch.id === branchId);

    if (nextIndex === -1) {
      return;
    }

    setSelectedBranchIdx(nextIndex);
    setShowFork(false);
    setShowFund(false);
  };

  const updateSelectedBranch = (updater: (branch: Branch) => Branch): void => {
    setBranches((current) =>
      current.map((branch, index) =>
        index === selectedBranchIdx ? updater(branch) : branch,
      ),
    );
  };

  const handleAddPosition = (payload: PositionEntrySubmit): void => {
    updateSelectedBranch((branch) => ({
      ...branch,
      positions: [
        ...branch.positions,
        {
          ...payload,
          id: createId("pos"),
        },
      ],
    }));
    setActiveTab("positions");
  };

  const handleUpdatePosition = (updatedPosition: Position): void => {
    updateSelectedBranch((branch) => ({
      ...branch,
      positions: branch.positions.map((position) =>
        position.id === updatedPosition.id ? updatedPosition : position,
      ),
    }));
  };

  const handleRemovePosition = (positionId: string): void => {
    updateSelectedBranch((branch) => ({
      ...branch,
      positions: branch.positions.filter((position) => position.id !== positionId),
    }));
  };

  const handleFund = ({ action, amount }: FundDialogSubmit): void => {
    updateSelectedBranch((branch) => ({
      ...branch,
      balance:
        action === "add"
          ? branch.balance + amount
          : Math.max(0, branch.balance - amount),
    }));
    setShowFund(false);
  };

  const handleCreateFork = ({ balance, forkDate, name }: ForkConfigSubmit): void => {
    if (!selectedBranch) {
      return;
    }

    const nextBranch: Branch = {
      id: createId("branch"),
      name,
      color: BRANCH_COLORS[branches.length % BRANCH_COLORS.length],
      isMain: false,
      parentId: selectedBranch.id,
      forkDate,
      balance,
      positions: selectedBranch.positions
        .filter((position) => posStateAt(position, forkDate).isActive)
        .map((position) => duplicatePosition(position)),
    };

    setBranches((current) => [...current, nextBranch]);
    setSelectedBranchIdx(branches.length);
    setShowFork(false);
    setActiveTab("positions");
  };

  const handleImportBranch = (branch: Branch): void => {
    const nextBranch = {
      ...branch,
      id: branch.id || createId("branch"),
      color: branch.color || BRANCH_COLORS[branches.length % BRANCH_COLORS.length],
    };

    setBranches((current) => [...current, nextBranch]);
    setSelectedBranchIdx(branches.length);
    setActiveTab("positions");
  };

  const tabOptions = [
    { id: "positions" as const, label: "Positions", count: selectedBranch?.positions.length ?? 0 },
    { id: "metrics" as const, label: "Metrics" },
    { id: "compare" as const, label: "Compare", count: branchData.length },
  ];

  return (
    <section className="space-y-5">
      <header className="flex flex-wrap items-end justify-between gap-4 border-b border-[--border-subtle] pb-4">
        <div className="space-y-2">
          <p className="section-header">Branches</p>
          <h1 className="text-2xl font-medium uppercase tracking-[0.08em] text-[--text-primary]">
            Portfolio branch workspace
          </h1>
        </div>
        <p className="mono-data text-xs text-[--text-secondary]">
          {selectedBranch ? `${selectedBranch.name} · ${selectedBranch.positions.length} positions` : "No branch selected"}
        </p>
      </header>

      <div className="grid gap-4 xl:grid-cols-[230px_minmax(0,1fr)]">
        <BranchSidebar
          branches={branchData}
          selectedBranchId={selectedBranch?.id}
          onSelectBranch={selectBranch}
          onFork={() => {
            setShowFork((current) => !current);
            setActiveTab("positions");
          }}
          onImport={() => setShowImport(true)}
        />

        <div className="min-w-0 space-y-4">
          <EquityChart
            branches={branchData}
            selectedBranchId={selectedBranch?.id}
            onSelectBranch={selectBranch}
          />

          {selectedBranch && selectedAccount ? (
            <>
              <AccountBar
                account={selectedAccount}
                branchLabel={selectedBranch.name}
              />

              <div className="flex flex-wrap items-center justify-between gap-3">
                <p className="mono-data text-xs text-[--text-secondary]">
                  Manage capital, add positions, then branch or compare outcomes.
                </p>
                <button
                  type="button"
                  onClick={() => setShowFund((current) => !current)}
                  className="ui-label border border-[--border] px-4 py-2.5 text-[--text-primary] transition-colors hover:bg-[--bg-hover]"
                >
                  {showFund ? "Hide funds" : "Manage funds"}
                </button>
              </div>

              <PositionEntry
                assetOptions={OHLC_ASSETS}
                onSubmit={handleAddPosition}
              />
            </>
          ) : null}

          <div className="panel overflow-hidden">
            <BranchTabs
              activeTab={activeTab}
              onChange={setActiveTab}
              tabs={tabOptions}
            />

            <div
              id={`branch-tabpanel-${activeTab}`}
              className="space-y-4 bg-[--bg-panel] p-4"
            >
              {activeTab === "positions" && selectedBranch && selectedAccount ? (
                <>
                  {showFork ? (
                    <ForkConfig
                      defaultDate={LATEST_DATE || selectedBranch.forkDate}
                      defaultName={`${selectedBranch.name} Fork`}
                      parentBranch={selectedBranch}
                      onCancel={() => setShowFork(false)}
                      onCreate={handleCreateFork}
                    />
                  ) : null}

                  {showFund ? (
                    <FundDialog
                      accountState={selectedAccount}
                      onCancel={() => setShowFund(false)}
                      onSubmit={handleFund}
                    />
                  ) : null}

                  {selectedBranch.positions.length > 0 ? (
                    <div className="space-y-3">
                      {selectedBranch.positions.map((position) => (
                        <PositionRow
                          key={position.id}
                          assetOptions={OHLC_ASSETS}
                          evaluationDate={evaluationDate}
                          onRemove={handleRemovePosition}
                          onUpdate={handleUpdatePosition}
                          position={position}
                          state={selectedPositionStates[position.id]}
                        />
                      ))}
                    </div>
                  ) : (
                    <div className="border border-dashed border-[--border] bg-[--bg-panel-alt] px-4 py-6">
                      <p className="section-header">No positions</p>
                      <p className="mono-data mt-2 text-sm text-[--text-secondary]">
                        Use the order panel above to add the first position to this branch.
                      </p>
                    </div>
                  )}
                </>
              ) : null}

              {activeTab === "metrics" && selectedMetrics && selectedAccount ? (
                <MetricsPanel
                  account={selectedAccount}
                  metrics={selectedMetrics}
                />
              ) : null}

              {activeTab === "compare" ? (
                <CompareTable
                  branches={branchData}
                  selectedBranchId={selectedBranch?.id}
                  onSelectBranch={(branchId) => {
                    selectBranch(branchId);
                    setActiveTab("compare");
                  }}
                />
              ) : null}
            </div>
          </div>
        </div>
      </div>

      <ImportDialog
        open={showImport}
        onClose={() => setShowImport(false)}
        onImport={(branch) => handleImportBranch(branch)}
      />
    </section>
  );
}
