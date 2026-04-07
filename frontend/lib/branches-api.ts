"use client";

import type { Branch, Position } from "@/lib/types";
import { resolveApiUrl } from "@/lib/ws";

interface RawPosition {
  id: string;
  asset: string;
  direction: Position["direction"];
  mode: Position["mode"];
  leverage: number;
  margin: number;
  entry_date: string;
  entry_price: number;
  exit_date?: string | null;
  exit_price?: number | null;
}

interface RawBranch {
  id: string;
  name: string;
  color: string;
  is_main: number | boolean;
  parent_id?: string | null;
  fork_date: string;
  balance: number;
  source_type?: string | null;
  source_path?: string | null;
  updated_at?: string | null;
  positions: RawPosition[];
}

const apiUrl = (): string => `${resolveApiUrl()}/api/branches`;

const parseJson = async <T>(response: Response): Promise<T> => {
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `Request failed: ${response.status}`);
  }
  return response.json() as Promise<T>;
};

export const normalizeBranch = (branch: RawBranch): Branch => ({
  id: branch.id,
  name: branch.name,
  color: branch.color,
  isMain: Boolean(branch.is_main),
  parentId: branch.parent_id ?? undefined,
  forkDate: branch.fork_date,
  balance: branch.balance,
  sourceType: branch.source_type ?? undefined,
  sourcePath: branch.source_path ?? undefined,
  updatedAt: branch.updated_at ?? undefined,
  positions: (branch.positions ?? []).map((position) => ({
    id: position.id,
    asset: position.asset,
    direction: position.direction,
    mode: position.mode,
    leverage: position.leverage,
    margin: position.margin,
    entryDate: position.entry_date,
    entryPrice: position.entry_price,
    exitDate: position.exit_date ?? undefined,
    exitPrice: position.exit_price ?? undefined,
  })),
});

export const fetchBranches = async (): Promise<Branch[]> => {
  const response = await fetch(apiUrl(), { cache: "no-store" });
  const payload = await parseJson<RawBranch[]>(response);
  return payload.map(normalizeBranch);
};

export const importBranchFile = async (
  rawText: string,
  fileName?: string,
): Promise<Branch> => {
  const response = await fetch(`${apiUrl()}/import`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      raw_text: rawText,
      file_name: fileName ?? null,
    }),
  });
  return normalizeBranch(await parseJson<RawBranch>(response));
};

export const createForkBranch = async (payload: {
  name: string;
  color: string;
  balance: number;
  parentId?: string;
}): Promise<Branch> => {
  const response = await fetch(`${apiUrl()}/fork`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      name: payload.name,
      color: payload.color,
      balance: payload.balance,
      parent_id: payload.parentId ?? null,
    }),
  });
  return normalizeBranch(await parseJson<RawBranch>(response));
};

export const updateBranch = async (
  branchId: string,
  payload: { name?: string; color?: string; balance?: number },
): Promise<Branch> => {
  const response = await fetch(`${apiUrl()}/${branchId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return normalizeBranch(await parseJson<RawBranch>(response));
};

export const addBranchPosition = async (
  branchId: string,
  position: Omit<Position, "id">,
): Promise<Position> => {
  const response = await fetch(`${apiUrl()}/${branchId}/positions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      asset: position.asset,
      direction: position.direction,
      mode: position.mode,
      leverage: position.leverage,
      margin: position.margin,
      entry_date: position.entryDate,
      entry_price: position.entryPrice,
      exit_date: position.exitDate ?? null,
      exit_price: position.exitPrice ?? null,
    }),
  });
  const raw = await parseJson<RawPosition>(response);
  return normalizeBranch({
    id: branchId,
    name: "",
    color: "",
    is_main: false,
    fork_date: "",
    balance: 0,
    positions: [raw],
  }).positions[0];
};

export const updateBranchPosition = async (
  branchId: string,
  position: Position,
): Promise<Position> => {
  const response = await fetch(`${apiUrl()}/${branchId}/positions/${position.id}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      asset: position.asset,
      direction: position.direction,
      mode: position.mode,
      leverage: position.leverage,
      margin: position.margin,
      entry_date: position.entryDate,
      entry_price: position.entryPrice,
      exit_date: position.exitDate ?? null,
      exit_price: position.exitPrice ?? null,
    }),
  });
  const raw = await parseJson<RawPosition>(response);
  return normalizeBranch({
    id: branchId,
    name: "",
    color: "",
    is_main: false,
    fork_date: "",
    balance: 0,
    positions: [raw],
  }).positions[0];
};

export const deleteBranchPosition = async (
  branchId: string,
  positionId: string,
): Promise<void> => {
  const response = await fetch(`${apiUrl()}/${branchId}/positions/${positionId}`, {
    method: "DELETE",
  });
  await parseJson<{ ok: boolean }>(response);
};
