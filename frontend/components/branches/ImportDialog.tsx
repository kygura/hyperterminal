"use client";

import { useCallback, useRef, useState } from "react";

import { fmt } from "@/lib/margin-engine";

import {
  parsePortfolioImport,
  type PortfolioImportParseResult,
} from "./import-parser";

export interface ImportDialogProps {
  open: boolean;
  onClose: () => void;
  onImport: (rawText: string, fileName?: string) => void;
}

const ACCEPTED_FILES = ".json,.yaml,.yml,application/json,text/yaml,text/x-yaml,application/x-yaml";

const dropzoneBaseClass =
  "flex min-h-[220px] flex-col items-center justify-center gap-3 border px-6 py-8 text-center transition-colors";

const formatLabel = (format: "json" | "yaml" | null | undefined): string => {
  if (format === "json") {
    return "JSON";
  }

  if (format === "yaml") {
    return "YAML";
  }

  return "—";
};

export function ImportDialog({
  open,
  onClose,
  onImport,
}: ImportDialogProps) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [selectedFileName, setSelectedFileName] = useState<string | null>(null);
  const [result, setResult] = useState<PortfolioImportParseResult | null>(null);

  const resetDialog = useCallback(() => {
    setIsDragging(false);
    setIsLoading(false);
    setSelectedFileName(null);
    setResult(null);
    if (inputRef.current) {
      inputRef.current.value = "";
    }
  }, []);

  const handleClose = useCallback(() => {
    resetDialog();
    onClose();
  }, [onClose, resetDialog]);

  const processText = useCallback((rawText: string, fileName?: string) => {
    setSelectedFileName(fileName ?? null);
    setResult(parsePortfolioImport(rawText, fileName));
  }, []);

  const readFile = useCallback(
    async (file: File) => {
      setIsLoading(true);

      try {
        const rawText = await file.text();
        processText(rawText, file.name);
      } catch {
        setSelectedFileName(file.name);
        setResult({
          success: false,
          rawText: "",
          format: null,
          errors: [
            {
              path: "document",
              message: "Unable to read the selected file.",
            },
          ],
        });
      } finally {
        setIsLoading(false);
      }
    },
    [processText],
  );

  const handleFileSelection = useCallback(
    async (event: React.ChangeEvent<HTMLInputElement>) => {
      const file = event.target.files?.[0];
      if (!file) {
        return;
      }

      await readFile(file);
    },
    [readFile],
  );

  const handleDrop = useCallback(
    async (event: React.DragEvent<HTMLDivElement>) => {
      event.preventDefault();
      setIsDragging(false);

      const file = event.dataTransfer.files?.[0];
      if (!file) {
        return;
      }

      await readFile(file);
    },
    [readFile],
  );

  const handleImport = useCallback(() => {
    if (!result?.success) {
      return;
    }

    onImport(result.parsed.rawText, selectedFileName ?? undefined);
    handleClose();
  }, [
    handleClose,
    onImport,
    result,
    selectedFileName,
  ]);

  if (!open) {
    return null;
  }

  const previewText = result?.success ? result.parsed.rawText : result?.rawText ?? "";
  const errorCount = result?.success ? 0 : result?.errors.length ?? 0;
  const invalidResult = result && !result.success ? result : null;

  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/70 px-4 py-6 backdrop-blur-sm">
      <div className="panel flex max-h-[90vh] w-full max-w-6xl flex-col overflow-hidden">
        <header className="flex items-start justify-between gap-4 border-b border-[--border] px-5 py-4">
          <div className="space-y-2">
            <p className="section-header">Import Portfolio</p>
            <h2 className="text-lg font-medium uppercase tracking-[0.08em] text-[--text-primary]">
              Import as Branch
            </h2>
            <p className="mono-data text-xs text-[--text-secondary]">
              Drop a YAML/JSON file or browse for one to validate before import.
            </p>
          </div>

          <button
            type="button"
            onClick={handleClose}
            className="ui-label border border-[--border] px-3 py-2 transition-colors hover:bg-[--bg-hover] hover:text-[--text-primary]"
          >
            Close
          </button>
        </header>

        <div className="grid min-h-0 flex-1 gap-px overflow-hidden bg-[--border] lg:grid-cols-[minmax(0,0.95fr)_minmax(0,1.05fr)]">
          <section className="min-h-0 overflow-y-auto bg-[--bg-panel] p-5">
            <div
              onDragEnter={(event) => {
                event.preventDefault();
                setIsDragging(true);
              }}
              onDragOver={(event) => {
                event.preventDefault();
                setIsDragging(true);
              }}
              onDragLeave={(event) => {
                event.preventDefault();
                if (event.currentTarget === event.target) {
                  setIsDragging(false);
                }
              }}
              onDrop={handleDrop}
              className={[
                dropzoneBaseClass,
                isDragging
                  ? "border-[--red-accent] bg-[--bg-elevated]"
                  : "border-[--border] bg-[--bg-panel-alt]",
              ].join(" ")}
            >
              <p className="ui-label">Drag and Drop</p>
              <p className="text-sm uppercase tracking-[0.08em] text-[--text-primary]">
                {isLoading ? "Reading file…" : "Drop portfolio file here"}
              </p>
              <p className="mono-data text-xs text-[--text-secondary]">
                Accepted formats: `.json`, `.yaml`, `.yml`
              </p>
              <button
                type="button"
                onClick={() => inputRef.current?.click()}
                className="ui-label border border-[--border] px-4 py-2 transition-colors hover:bg-[--bg-hover] hover:text-[--text-primary]"
              >
                Choose File
              </button>
              <input
                ref={inputRef}
                type="file"
                accept={ACCEPTED_FILES}
                className="hidden"
                onChange={handleFileSelection}
              />
            </div>

            <div className="mt-5 grid gap-3 sm:grid-cols-3">
              <div className="border border-[--border] bg-[--bg-panel-alt] p-3">
                <p className="ui-label">File</p>
                <p className="mono-data mt-2 truncate text-sm text-[--text-primary]">
                  {selectedFileName ?? "No file selected"}
                </p>
              </div>
              <div className="border border-[--border] bg-[--bg-panel-alt] p-3">
                <p className="ui-label">Format</p>
                <p className="mono-data mt-2 text-sm text-[--text-primary]">
                  {formatLabel(result?.success ? result.parsed.format : result?.format)}
                </p>
              </div>
              <div className="border border-[--border] bg-[--bg-panel-alt] p-3">
                <p className="ui-label">Status</p>
                <p
                  className={[
                    "mono-data mt-2 text-sm",
                    result?.success
                      ? "text-[--green]"
                      : errorCount > 0
                        ? "text-[--red]"
                        : "text-[--text-secondary]",
                  ].join(" ")}
                >
                  {result?.success
                    ? "Ready to import"
                    : errorCount > 0
                      ? `${errorCount} validation ${errorCount === 1 ? "error" : "errors"}`
                      : "Awaiting file"}
                </p>
              </div>
            </div>

            {result?.success ? (
              <div className="mt-5 space-y-4">
                <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                  <div className="border border-[--border] bg-[--bg-panel-alt] p-3">
                    <p className="ui-label">Portfolio</p>
                    <p className="mono-data mt-2 text-sm text-[--text-primary]">
                      {result.parsed.summary.name}
                    </p>
                  </div>
                  <div className="border border-[--border] bg-[--bg-panel-alt] p-3">
                    <p className="ui-label">Balance</p>
                    <p className="mono-data mt-2 text-sm text-[--text-primary]">
                      ${fmt(result.parsed.summary.balance)}
                    </p>
                  </div>
                  <div className="border border-[--border] bg-[--bg-panel-alt] p-3">
                    <p className="ui-label">Positions</p>
                    <p className="mono-data mt-2 text-sm text-[--text-primary]">
                      {result.parsed.summary.totalPositions}
                    </p>
                  </div>
                  <div className="border border-[--border] bg-[--bg-panel-alt] p-3">
                    <p className="ui-label">Assets</p>
                    <p className="mono-data mt-2 text-sm text-[--text-primary]">
                      {result.parsed.summary.assets.join(", ") || "—"}
                    </p>
                  </div>
                </div>

                <div className="border border-[--border] bg-[--bg-panel-alt]">
                  <div className="flex items-center justify-between gap-3 border-b border-[--border] px-3 py-2">
                    <p className="section-header">Position Preview</p>
                    <p className="mono-data text-xs text-[--text-secondary]">
                      {result.parsed.summary.openPositions} open ·{" "}
                      {result.parsed.summary.closedPositions} closed
                    </p>
                  </div>
                  <div className="max-h-[280px] overflow-auto">
                    <table className="w-full border-collapse text-left">
                      <thead className="sticky top-0 bg-[--bg-panel]">
                        <tr className="border-b border-[--border]">
                          {["Asset", "Dir", "Mode", "Lev", "Margin", "Entry", "Exit"].map(
                            (label) => (
                              <th
                                key={label}
                                className="ui-label px-3 py-2 text-[11px]"
                              >
                                {label}
                              </th>
                            ),
                          )}
                        </tr>
                      </thead>
                      <tbody>
                        {result.parsed.value.portfolio.positions.map((position, index) => (
                          <tr
                            key={`${position.asset}-${position.entry_date}-${index}`}
                            className="border-b border-[--border-subtle] last:border-b-0"
                          >
                            <td className="mono-data px-3 py-2 text-sm text-[--text-primary]">
                              {position.asset}
                            </td>
                            <td
                              className={[
                                "mono-data px-3 py-2 text-sm",
                                position.direction === "Long"
                                  ? "text-[--green]"
                                  : "text-[--red]",
                              ].join(" ")}
                            >
                              {position.direction}
                            </td>
                            <td className="mono-data px-3 py-2 text-sm text-[--text-muted]">
                              {position.mode ?? "Cross"}
                            </td>
                            <td className="mono-data px-3 py-2 text-sm text-[--text-primary]">
                              {position.leverage}×
                            </td>
                            <td className="mono-data px-3 py-2 text-sm text-[--text-primary]">
                              ${fmt(position.margin)}
                            </td>
                            <td className="mono-data px-3 py-2 text-sm text-[--text-primary]">
                              {position.entry_date} @ ${fmt(position.entry_price)}
                            </td>
                            <td className="mono-data px-3 py-2 text-sm text-[--text-secondary]">
                              {position.exit_date && position.exit_price
                                ? `${position.exit_date} @ $${fmt(position.exit_price)}`
                                : "Open"}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              </div>
            ) : invalidResult ? (
              <div className="mt-5 border border-[--red]/50 bg-[--red]/10 p-4">
                <div className="flex items-center justify-between gap-3">
                  <p className="section-header text-[--red]">Validation Errors</p>
                  <p className="mono-data text-xs text-[--text-secondary]">
                    Fix the file and try again.
                  </p>
                </div>
                <ul className="mt-3 space-y-2">
                  {invalidResult.errors.map((issue) => (
                    <li
                      key={`${issue.path}-${issue.message}`}
                      className="border border-[--border] bg-[--bg-panel] px-3 py-2"
                    >
                      <p className="mono-data text-xs text-[--red]">{issue.path}</p>
                      <p className="mono-data mt-1 text-sm text-[--text-primary]">
                        {issue.message}
                      </p>
                    </li>
                  ))}
                </ul>
              </div>
            ) : (
              <div className="mt-5 border border-dashed border-[--border] bg-[--bg-panel-alt] px-4 py-6">
                <p className="section-header">Import Preview</p>
                <p className="mono-data mt-2 text-sm text-[--text-secondary]">
                  Select a file to preview parsed portfolio data before importing it as
                  a branch.
                </p>
              </div>
            )}
          </section>

          <section className="min-h-0 overflow-y-auto bg-[--bg-panel] p-5">
            <div className="flex items-center justify-between gap-3">
              <div>
                <p className="section-header">Content Preview</p>
                <p className="mono-data mt-2 text-xs text-[--text-secondary]">
                  Raw import content stays visible for quick validation.
                </p>
              </div>
              {previewText ? (
                <span className="mono-data text-xs text-[--text-secondary]">
                  {previewText.length.toLocaleString("en-US")} chars
                </span>
              ) : null}
            </div>

            <textarea
              readOnly
              value={previewText}
              placeholder="Imported file contents will appear here."
              className="mono-data mt-4 min-h-[520px] w-full resize-none border border-[--border] bg-[--bg-panel-alt] p-4 text-xs leading-6 text-[--text-muted] outline-none"
            />
          </section>
        </div>

        <footer className="flex items-center justify-between gap-4 border-t border-[--border] px-5 py-4">
          <p className="mono-data text-xs text-[--text-secondary]">
            {result?.success
              ? "Portfolio import passed schema validation."
              : "Import requires a valid PortfolioImport YAML/JSON document."}
          </p>

          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={handleClose}
              className="ui-label border border-[--border] px-4 py-2 transition-colors hover:bg-[--bg-hover] hover:text-[--text-primary]"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={handleImport}
              disabled={!result?.success}
              className={[
                "ui-label px-4 py-2 transition-opacity",
                result?.success
                  ? "bg-[--red-accent] text-[--text-primary]"
                  : "cursor-not-allowed bg-[--border] text-[--text-secondary] opacity-60",
              ].join(" ")}
            >
              Import as Branch
            </button>
          </div>
        </footer>
      </div>
    </div>
  );
}

export default ImportDialog;
