// @ts-expect-error -- js-yaml is installed in the frontend package, but this repo does not include its type declarations.
import { load } from "js-yaml";

import type { Branch, PortfolioImport, Position } from "@/lib/types";
import { ASSETS } from "@/lib/types";

export type ImportFormat = "json" | "yaml";

export interface ValidationIssue {
  path: string;
  message: string;
}

export interface PortfolioImportSummary {
  name: string;
  balance: number;
  totalPositions: number;
  openPositions: number;
  closedPositions: number;
  assets: string[];
}

export interface ParsedPortfolioImport {
  format: ImportFormat;
  rawText: string;
  value: PortfolioImport;
  summary: PortfolioImportSummary;
}

export type PortfolioImportParseResult =
  | {
      success: true;
      parsed: ParsedPortfolioImport;
    }
  | {
      success: false;
      rawText: string;
      format: ImportFormat | null;
      errors: ValidationIssue[];
    };

export interface BranchImportOptions {
  branchId?: string;
  color?: string;
  parentId?: string;
  isMain?: boolean;
  forkDate?: string;
}

const IMPORT_BRANCH_COLORS = [
  "#ed3602",
  "#38a67c",
  "#627eea",
  "#f7931a",
  "#9945ff",
  "#50d2c1",
];

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const isFiniteNumber = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value);

const normalizeDateString = (value: string): string | null => {
  const parsed = new Date(value);

  if (Number.isNaN(parsed.getTime())) {
    return null;
  }

  return parsed.toISOString().slice(0, 10);
};

const inferFormat = (rawText: string, fileName?: string): ImportFormat => {
  const lowerName = fileName?.toLowerCase();

  if (lowerName?.endsWith(".json")) {
    return "json";
  }

  if (lowerName?.endsWith(".yaml") || lowerName?.endsWith(".yml")) {
    return "yaml";
  }

  const trimmed = rawText.trimStart();
  return trimmed.startsWith("{") || trimmed.startsWith("[") ? "json" : "yaml";
};

const createIssue = (path: string, message: string): ValidationIssue => ({
  path,
  message,
});

const parseRawDocument = (
  rawText: string,
  format: ImportFormat,
): { value?: unknown; errors: ValidationIssue[] } => {
  try {
    return {
      value:
        format === "json"
          ? JSON.parse(rawText)
          : load(rawText, { json: true }),
      errors: [],
    };
  } catch (error) {
    const message =
      error instanceof Error ? error.message : "Unable to parse import document.";

    return {
      errors: [
        createIssue(
          "document",
          `Invalid ${format.toUpperCase()} syntax: ${message}`,
        ),
      ],
    };
  }
};

const validatePosition = (
  value: unknown,
  index: number,
  errors: ValidationIssue[],
): PortfolioImport["portfolio"]["positions"][number] | null => {
  const basePath = `portfolio.positions[${index}]`;

  if (!isRecord(value)) {
    errors.push(createIssue(basePath, "Each position must be an object."));
    return null;
  }

  const assetValue = value.asset;
  const asset = typeof assetValue === "string" ? assetValue.trim().toUpperCase() : "";

  if (!asset) {
    errors.push(createIssue(`${basePath}.asset`, "Asset is required."));
  } else if (!(asset in ASSETS)) {
    errors.push(
      createIssue(
        `${basePath}.asset`,
        `Unsupported asset "${asset}". Expected one of ${Object.keys(ASSETS).join(", ")}.`,
      ),
    );
  }

  const directionValue = value.direction;
  const direction =
    directionValue === "Long" || directionValue === "Short"
      ? directionValue
      : null;
  if (direction !== "Long" && direction !== "Short") {
    errors.push(
      createIssue(
        `${basePath}.direction`,
        'Direction must be "Long" or "Short".',
      ),
    );
  }

  const modeValue = value.mode;
  const mode =
    modeValue === undefined ? "Cross" : modeValue === "Cross" || modeValue === "Isolated" ? modeValue : null;

  if (modeValue !== undefined && mode === null) {
    errors.push(
      createIssue(
        `${basePath}.mode`,
        'Mode must be "Cross" or "Isolated" when provided.',
      ),
    );
  }

  const leverageValue = value.leverage;
  const leverage = isFiniteNumber(leverageValue) ? leverageValue : null;
  if (leverage === null || leverage <= 0) {
    errors.push(
      createIssue(`${basePath}.leverage`, "Leverage must be a positive number."),
    );
  } else if (asset && asset in ASSETS && leverage > ASSETS[asset].maxLeverage) {
    errors.push(
      createIssue(
        `${basePath}.leverage`,
        `${asset} leverage cannot exceed ${ASSETS[asset].maxLeverage}×.`,
      ),
    );
  }

  const marginValue = value.margin;
  const margin = isFiniteNumber(marginValue) ? marginValue : null;
  if (margin === null || margin <= 0) {
    errors.push(
      createIssue(`${basePath}.margin`, "Margin must be a positive number."),
    );
  }

  const entryDateValue = value.entry_date;
  const entryDate =
    typeof entryDateValue === "string" ? normalizeDateString(entryDateValue) : null;
  if (!entryDate) {
    errors.push(
      createIssue(
        `${basePath}.entry_date`,
        "Entry date must be a valid date string.",
      ),
    );
  }

  const entryPriceValue = value.entry_price;
  const entryPrice = isFiniteNumber(entryPriceValue) ? entryPriceValue : null;
  if (entryPrice === null || entryPrice <= 0) {
    errors.push(
      createIssue(
        `${basePath}.entry_price`,
        "Entry price must be a positive number.",
      ),
    );
  }

  const exitDateValue = value.exit_date;
  const exitDate =
    exitDateValue === undefined
      ? undefined
      : typeof exitDateValue === "string"
        ? normalizeDateString(exitDateValue)
        : null;

  if (exitDateValue !== undefined && !exitDate) {
    errors.push(
      createIssue(
        `${basePath}.exit_date`,
        "Exit date must be a valid date string when provided.",
      ),
    );
  }

  const exitPriceValue = value.exit_price;
  const exitPrice =
    exitPriceValue === undefined
      ? undefined
      : isFiniteNumber(exitPriceValue)
        ? exitPriceValue
        : null;

  if (
    exitPriceValue !== undefined &&
    (typeof exitPrice !== "number" || exitPrice <= 0)
  ) {
    errors.push(
      createIssue(
        `${basePath}.exit_price`,
        "Exit price must be a positive number when provided.",
      ),
    );
  }

  if (exitDateValue !== undefined && exitPriceValue === undefined) {
    errors.push(
      createIssue(
        `${basePath}.exit_price`,
        "Exit price is required when exit date is provided.",
      ),
    );
  }

  if (exitPriceValue !== undefined && exitDateValue === undefined) {
    errors.push(
      createIssue(
        `${basePath}.exit_date`,
        "Exit date is required when exit price is provided.",
      ),
    );
  }

  if (entryDate && exitDate && exitDate < entryDate) {
    errors.push(
      createIssue(
        `${basePath}.exit_date`,
        "Exit date cannot be earlier than entry date.",
      ),
    );
  }

  if (errors.some((issue) => issue.path.startsWith(basePath))) {
    return null;
  }

  return {
    asset,
    direction: direction!,
    mode: mode ?? "Cross",
    leverage: leverage!,
    margin: margin!,
    entry_date: entryDate!,
    entry_price: entryPrice!,
    exit_date: exitDate ?? undefined,
    exit_price: exitPrice ?? undefined,
  };
};

const validatePortfolioImport = (
  value: unknown,
): { value?: PortfolioImport; errors: ValidationIssue[] } => {
  const errors: ValidationIssue[] = [];

  if (!isRecord(value)) {
    return {
      errors: [createIssue("document", "Document root must be an object.")],
    };
  }

  const portfolioValue = value.portfolio;

  if (!isRecord(portfolioValue)) {
    return {
      errors: [
        createIssue("portfolio", "portfolio is required and must be an object."),
      ],
    };
  }

  const nameValue = portfolioValue.name;
  const name = typeof nameValue === "string" ? nameValue.trim() : "";
  if (!name) {
    errors.push(
      createIssue("portfolio.name", "Portfolio name is required."),
    );
  }

  const balanceValue = portfolioValue.balance;
  const balance = isFiniteNumber(balanceValue) ? balanceValue : null;
  if (balance === null || balance < 0) {
    errors.push(
      createIssue(
        "portfolio.balance",
        "Portfolio balance must be a non-negative number.",
      ),
    );
  }

  const positionsValue = portfolioValue.positions;
  if (!Array.isArray(positionsValue)) {
    errors.push(
      createIssue(
        "portfolio.positions",
        "Portfolio positions must be an array.",
      ),
    );
  }

  const positions =
    Array.isArray(positionsValue)
      ? positionsValue
          .map((position, index) => validatePosition(position, index, errors))
          .filter(
            (
              position,
            ): position is PortfolioImport["portfolio"]["positions"][number] =>
              position !== null,
          )
      : [];

  if (errors.length > 0) {
    return { errors };
  }

  return {
    value: {
      portfolio: {
        name,
        balance: balance!,
        positions,
      },
    },
    errors: [],
  };
};

const createId = (prefix: string): string =>
  globalThis.crypto?.randomUUID?.() ??
  `${prefix}_${Math.random().toString(36).slice(2, 10)}`;

const defaultBranchColor = (name: string): string => {
  const codePoints = Array.from(name).reduce(
    (sum, char) => sum + char.charCodeAt(0),
    0,
  );
  return IMPORT_BRANCH_COLORS[codePoints % IMPORT_BRANCH_COLORS.length];
};

export const summarizePortfolioImport = (
  value: PortfolioImport,
): PortfolioImportSummary => {
  const assets = Array.from(
    new Set(value.portfolio.positions.map((position) => position.asset)),
  );
  const closedPositions = value.portfolio.positions.filter(
    (position) => position.exit_date,
  ).length;

  return {
    name: value.portfolio.name,
    balance: value.portfolio.balance,
    totalPositions: value.portfolio.positions.length,
    openPositions: value.portfolio.positions.length - closedPositions,
    closedPositions,
    assets,
  };
};

export const parsePortfolioImport = (
  rawText: string,
  fileName?: string,
): PortfolioImportParseResult => {
  if (!rawText.trim()) {
    return {
      success: false,
      rawText,
      format: null,
      errors: [createIssue("document", "Import file is empty.")],
    };
  }

  const format = inferFormat(rawText, fileName);
  const parsedDocument = parseRawDocument(rawText, format);

  if (parsedDocument.errors.length > 0) {
    return {
      success: false,
      rawText,
      format,
      errors: parsedDocument.errors,
    };
  }

  const validation = validatePortfolioImport(parsedDocument.value);

  if (validation.errors.length > 0 || !validation.value) {
    return {
      success: false,
      rawText,
      format,
      errors: validation.errors,
    };
  }

  return {
    success: true,
    parsed: {
      format,
      rawText,
      value: validation.value,
      summary: summarizePortfolioImport(validation.value),
    },
  };
};

const inferForkDate = (portfolio: PortfolioImport["portfolio"]): string => {
  const dates = portfolio.positions.map((position) => position.entry_date).sort();
  return dates[0] ?? new Date().toISOString().slice(0, 10);
};

export const portfolioImportToBranch = (
  value: PortfolioImport,
  options: BranchImportOptions = {},
): Branch => {
  const positions: Position[] = value.portfolio.positions.map((position, index) => ({
    id: createId(`pos_${index + 1}`),
    asset: position.asset,
    direction: position.direction,
    mode: position.mode ?? "Cross",
    leverage: position.leverage,
    margin: position.margin,
    entryDate: position.entry_date,
    entryPrice: position.entry_price,
    exitDate: position.exit_date,
    exitPrice: position.exit_price,
  }));

  return {
    id: options.branchId ?? createId("branch"),
    name: value.portfolio.name,
    color: options.color ?? defaultBranchColor(value.portfolio.name),
    isMain: options.isMain ?? false,
    parentId: options.parentId,
    forkDate: options.forkDate ?? inferForkDate(value.portfolio),
    balance: value.portfolio.balance,
    positions,
  };
};
