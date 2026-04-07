"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

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
  addBranchPosition,
  createForkBranch,
  deleteBranchPosition,
  fetchBranches,
  importBranchFile,
  updateBranch,
  updateBranchPosition,
} from "@/lib/branches-api";
import {
  accountStateAt,
  computeAllBranches,
  posStateAt,
} from "@/lib/margin-engine";
import {
  OHLC_ASSETS,
  OHLC_DATES,
  setOhlcDataset,
  type OhlcDataset,
} from "@/lib/price-data";
import type { Branch, Position } from "@/lib/types";
import { resolveApiUrl } from "@/lib/ws";

const BRANCH_COLORS = ["#ed3602", "#38a67c", "#627eea", "#f7931a", "#9945ff", "#50d2c1"];
const BRANCH_POLL_MS = 5000;

export default function BranchesPage() {
  const [branches, setBranches] = useState<Branch[]>([]);
  const [selectedBranchId, setSelectedBranchId] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<BranchTab>("positions");
  const [showFork, setShowFork] = useState(false);
  const [showImport, setShowImport] = useState(false);
  const [showFund, setShowFund] = useState(false);
  const [, setPriceDataVersion] = useState(0);

  const refreshBranches = useCallback(async () => {
    const nextBranches = await fetchBranches();
    setBranches(nextBranches);
    setSelectedBranchId((current) =>
      current && nextBranches.some((branch) => branch.id === current)
        ? current
        : nextBranches[0]?.id ?? null,
    );
  }, []);

  useEffect(() => {
    const controller = new AbortController();

    void fetch(`${resolveApiUrl()}/api/branches/price-history?timeframe=1d`, {
      signal: controller.signal,
    })
      .then(async (response) => {
        if (!response.ok) {
          throw new Error(`price history request failed: ${response.status}`);
        }
        return response.json();
      })
      .then((payload: OhlcDataset) => {
        if (!payload || typeof payload !== "object" || !payload.assets) {
          return;
        }
        setOhlcDataset(payload);
        setPriceDataVersion((current) => current + 1);
      })
      .catch(() => undefined);

    return () => controller.abort();
  }, []);

  useEffect(() => {
    void refreshBranches();
    const intervalId = window.setInterval(() => {
      void refreshBranches();
    }, BRANCH_POLL_MS);
    return () => window.clearInterval(intervalId);
  }, [refreshBranches]);

  const latestDate = OHLC_DATES[OHLC_DATES.length - 1] ?? "";
  const branchData = useMemo(() => computeAllBranches(branches), [branches]);
  const selectedBranch = branches.find((branch) => branch.id === selectedBranchId) ?? branches[0] ?? null;
  const selectedMetrics =
    branchData.find((metric) => metric.branch.id === selectedBranch?.id) ?? branchData[0] ?? null;
  const evaluationDate = latestDate || selectedBranch?.forkDate || "";

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

  const selectBranch = useCallback((branchId: string) => {
    setSelectedBranchId(branchId);
    setShowFork(false);
    setShowFund(false);
  }, []);

  const handleAddPosition = useCallback(
    async (payload: PositionEntrySubmit) => {
      if (!selectedBranch) {
        return;
      }
      await addBranchPosition(selectedBranch.id, payload);
      await refreshBranches();
      setActiveTab("positions");
    },
    [refreshBranches, selectedBranch],
  );

  const handleUpdatePosition = useCallback(
    async (updatedPosition: Position) => {
      if (!selectedBranch) {
        return;
      }
      await updateBranchPosition(selectedBranch.id, updatedPosition);
      await refreshBranches();
    },
    [refreshBranches, selectedBranch],
  );

  const handleRemovePosition = useCallback(
    async (positionId: string) => {
      if (!selectedBranch) {
        return;
      }
      await deleteBranchPosition(selectedBranch.id, positionId);
      await refreshBranches();
    },
    [refreshBranches, selectedBranch],
  );

  const handleFund = useCallback(
    async ({ action, amount }: FundDialogSubmit) => {
      if (!selectedBranch) {
        return;
      }
      const nextBalance =
        action === "add"
          ? selectedBranch.balance + amount
          : Math.max(0, selectedBranch.balance - amount);
      await updateBranch(selectedBranch.id, { balance: nextBalance });
      await refreshBranches();
      setShowFund(false);
    },
    [refreshBranches, selectedBranch],
  );

  const handleCreateFork = useCallback(
    async ({ balance, name }: ForkConfigSubmit) => {
      if (!selectedBranch) {
        return;
      }
      const created = await createForkBranch({
        name,
        color: BRANCH_COLORS[branches.length % BRANCH_COLORS.length],
        balance,
        parentId: selectedBranch.id,
      });
      await refreshBranches();
      setSelectedBranchId(created.id);
      setShowFork(false);
      setActiveTab("positions");
    },
    [branches.length, refreshBranches, selectedBranch],
  );

  const handleImportBranch = useCallback(
    async (rawText: string, fileName?: string) => {
      const imported = await importBranchFile(rawText, fileName);
      await refreshBranches();
      setSelectedBranchId(imported.id);
      setActiveTab("positions");
    },
    [refreshBranches],
  );

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
          {selectedBranch ? `${selectedBranch.name} · ${selectedBranch.positions.length} positions` : "Loading branches"}
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
                onSubmit={(payload) => {
                  void handleAddPosition(payload);
                }}
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
                      defaultDate={latestDate || selectedBranch.forkDate}
                      defaultName={`${selectedBranch.name} Fork`}
                      parentBranch={selectedBranch}
                      onCancel={() => setShowFork(false)}
                      onCreate={(payload) => {
                        void handleCreateFork(payload);
                      }}
                    />
                  ) : null}

                  {showFund ? (
                    <FundDialog
                      accountState={selectedAccount}
                      onCancel={() => setShowFund(false)}
                      onSubmit={(payload) => {
                        void handleFund(payload);
                      }}
                    />
                  ) : null}

                  {selectedBranch.positions.length > 0 ? (
                    <div className="space-y-3">
                      {selectedBranch.positions.map((position) => (
                        <PositionRow
                          key={position.id}
                          assetOptions={OHLC_ASSETS}
                          evaluationDate={evaluationDate}
                          onRemove={(positionId) => {
                            void handleRemovePosition(positionId);
                          }}
                          onUpdate={(positionToSave) => {
                            void handleUpdatePosition(positionToSave);
                          }}
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
        onImport={(rawText, fileName) => {
          void handleImportBranch(rawText, fileName);
        }}
      />
    </section>
  );
}
