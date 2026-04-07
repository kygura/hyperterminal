"use client";

import { useCallback, useEffect, useState } from "react";
import {
  deleteIndicator,
  loadIndicators,
  type SavedIndicator,
} from "@/lib/charts";

interface ActiveEntry {
  id: string;
  name: string;
}

interface Props {
  activeIndicators: ActiveEntry[];
  onLoad: (code: string) => void;
  onRemove: (id: string) => void;
}

export default function IndicatorList({
  activeIndicators,
  onLoad,
  onRemove,
}: Props) {
  const [saved, setSaved] = useState<SavedIndicator[]>([]);

  const refresh = useCallback(() => setSaved(loadIndicators()), []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const handleDelete = useCallback(
    (name: string) => {
      deleteIndicator(name);
      refresh();
    },
    [refresh],
  );

  return (
    <div className="space-y-4">
      {activeIndicators.length > 0 && (
        <div className="space-y-2">
          <p className="section-header">Active</p>
          {activeIndicators.map((entry) => (
            <div
              key={entry.id}
              className="flex items-center justify-between border border-[--border-subtle] bg-[--bg-panel-alt] px-3 py-2"
            >
              <span className="mono-data text-xs text-[--text-primary]">
                {entry.name}
              </span>
              <button
                type="button"
                onClick={() => onRemove(entry.id)}
                className="ui-label text-[10px] text-[--red] hover:text-[--red-accent]"
              >
                Remove
              </button>
            </div>
          ))}
        </div>
      )}

      <div className="space-y-2">
        <p className="section-header">
          Saved{" "}
          <span className="text-[--text-secondary]">({saved.length})</span>
        </p>

        {saved.length === 0 ? (
          <p className="mono-data text-xs text-[--text-secondary]">
            No saved indicators. Write and save one above.
          </p>
        ) : (
          saved.map((item) => (
            <div
              key={item.name}
              className="flex items-center justify-between gap-2 border border-[--border-subtle] bg-[--bg-panel-alt] px-3 py-2"
            >
              <span className="mono-data truncate text-xs text-[--text-primary]">
                {item.name}
              </span>
              <div className="flex shrink-0 gap-3">
                <button
                  type="button"
                  onClick={() => onLoad(item.code)}
                  className="ui-label text-[10px] text-[--text-muted] hover:text-[--text-primary]"
                >
                  Load
                </button>
                <button
                  type="button"
                  onClick={() => handleDelete(item.name)}
                  className="ui-label text-[10px] text-[--red] hover:text-[--red-accent]"
                >
                  Delete
                </button>
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
