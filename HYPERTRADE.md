# HYPERTRADE — Parallel Build Orchestration Prompt

> A focused trading terminal: signal viewer + portfolio branching/backtesting engine.
> Design language cloned from **hyperdash.com**.

---

## Orchestration Model

This project is built by a **master orchestrator agent** that spawns parallel subagents.
Each task below is a self-contained unit with:
- **ID**: unique task identifier
- **Dependencies**: which tasks must complete first (by ID)
- **Owns**: files/directories this agent creates (no other agent touches these)
- **Inputs**: what it reads from other tasks' outputs
- **Outputs**: what it produces for downstream tasks
- **Acceptance**: how the orchestrator verifies completion

**Collision rule**: No two agents write to the same file. Shared contracts (types, tokens) are produced by a dedicated task and consumed read-only by others.

---

## Dependency Graph

```
T0 (Scaffold + Contracts)
├── T1 (Signal Backend)          ── can run parallel ──  T3 (Margin Engine)
│                                                        │
├── T2 (Signal Frontend)                                 ├── T4 (Branch UI: Sidebar + Chart + Tabs)
│   └── depends on T0, T1                               │   └── depends on T0, T3
│                                                        │
│                                                        ├── T5 (Branch UI: Position Entry + Rows)
│                                                        │   └── depends on T0, T3
│                                                        │
│                                                        ├── T6 (Import System)
│                                                        │   └── depends on T0, T3
│                                                        │
│                                                        └── T7 (Branch Page Assembly)
│                                                            └── depends on T4, T5, T6
│
├── T8 (Telegram Forwarding)     ── independent ──
│   └── depends on T1
│
└── T9 (Docker Compose)
    └── depends on ALL
```

**Parallelism plan:**
- **Wave 1**: T0
- **Wave 2**: T1, T3 (parallel)
- **Wave 3**: T2, T4, T5, T6, T8 (parallel — all have satisfied deps after wave 2)
- **Wave 4**: T7
- **Wave 5**: T9

---

## Design System (shared reference — all agents read this)

### Tokens

```css
:root {
  --bg-body:       #100e0a;
  --bg-panel:      #111111;
  --bg-panel-alt:  #0d0d0d;
  --bg-elevated:   #191613;
  --bg-hover:      rgba(255, 255, 255, 0.04);
  --border:        rgba(255, 255, 255, 0.08);
  --border-subtle: rgba(255, 255, 255, 0.04);
  --text-primary:  #ffffff;
  --text-secondary:#928d86;
  --text-muted:    #d5d1cd;
  --green:         #38a67c;
  --red:           #bc263e;
  --red-accent:    #ed3602;
  --amber:         #ffb800;
}
```

### Typography
- **UI labels**: `'Inter', system-ui, sans-serif` — uppercase, `letter-spacing: 0.05em`, `font-weight: 500`, `text-xs`
- **Data values**: `'JetBrains Mono', monospace` — `tabular-nums`
- **Section headers**: ALL CAPS, `text-xs`, `text-[--text-secondary]`, `tracking-wider`

### Component Patterns
1. **Panels**: `bg-[--bg-panel]`, `border border-[--border]`, no border-radius or `rounded-sm` max
2. **Stat rows**: Label left (`text-[--text-secondary]`), value right (`font-mono text-[--text-primary]`)
3. **Tab bars**: ALL CAPS, active = `border-b-2 border-[--red-accent]`, inactive = `text-[--text-secondary]`
4. **PnL**: Positive = `text-[--green]`, Negative = `text-[--red]`. Always.
5. **Inputs**: `bg-[--bg-body] border border-[--border]`, `font-mono`, dark color-scheme
6. **Long/Short**: Side-by-side buttons. Long = green bg. Short = red bg. Uppercase.
7. **Badges**: Small pills. Direction = green/red bg at 10% opacity + colored text. Mode = amber (Cross) / teal (Isolated)

---

## T0 — Scaffold + Shared Contracts

**Dependencies**: None (runs first)
**Agents**: 1

### Owns
```
frontend/
├── app/layout.tsx
├── app/page.tsx              # redirect to /signals
├── app/globals.css           # design tokens as CSS vars
├── components/shell/
│   ├── TopNav.tsx
│   └── BottomTicker.tsx
├── lib/types.ts              # ALL shared TypeScript interfaces
├── tailwind.config.ts
├── next.config.js
└── package.json
```

### Tasks

1. `npx create-next-app@latest frontend --typescript --tailwind --app --src-dir=false --import-alias="@/*"`
2. Install: `ws reconnecting-websocket js-yaml`
3. Configure Tailwind with design tokens (colors, fonts)
4. Load Inter + JetBrains Mono via `next/font/google`
5. `globals.css`: CSS custom properties for all tokens
6. `lib/types.ts` — define ALL shared interfaces:

```typescript
// === Signal types ===
export interface Signal {
  id: string;
  timestamp: string;
  asset: string;
  direction: "Long" | "Short";
  strength: number;
  type: string;
  meta: {
    bid_volume: number;
    ask_volume: number;
    ratio: number;
    timeframe: string;
  };
}

// === Branch types ===
export interface Position {
  id: string;
  asset: string;
  direction: "Long" | "Short";
  mode: "Cross" | "Isolated";
  leverage: number;
  margin: number;
  entryDate: string;
  entryPrice: number;
  exitDate?: string;
  exitPrice?: number;
}

export interface Branch {
  id: string;
  name: string;
  color: string;
  isMain: boolean;
  parentId?: string;
  forkDate: string;
  balance: number;
  positions: Position[];
}

export interface BranchMetrics {
  branch: Branch;
  eq: number[];
  forkIdx: number;
  ret: number;
  mdd: number;
  sharpe: number;
  val: number;
}

export interface AccountState {
  crossEquity: number;
  totalEquity: number;
  availableMargin: number;
  crossMarginUsed: number;
  isoMarginUsed: number;
  crossUPnL: number;
  isoUPnL: number;
  crossMM: number;
  crossLiquidated: boolean;
  maxWithdraw: number;
  isoLosses: number;
}

export interface PositionState {
  isActive: boolean;
  isClosed: boolean;
  mark: number;
  entry: number;
  notional: number;
  pnlPct: number;
  unrealizedPnl: number;
  posEquity: number;
  maintenanceMargin: number;
  isLiquidated: boolean;
  entryIdx: number;
  exitIdx: number | null;
}

export interface PortfolioImport {
  portfolio: {
    name: string;
    balance: number;
    positions: Array<{
      asset: string;
      direction: "Long" | "Short";
      mode?: "Cross" | "Isolated";
      leverage: number;
      margin: number;
      entry_date: string;
      entry_price: number;
      exit_date?: string;
      exit_price?: number;
    }>;
  };
}

export interface AssetConfig {
  name: string;
  color: string;
  maxLeverage: number;
}

export const ASSETS: Record<string, AssetConfig> = {
  BTC:  { name: "BTC-USD",  color: "#f7931a", maxLeverage: 50 },
  ETH:  { name: "ETH-USD",  color: "#627eea", maxLeverage: 25 },
  SOL:  { name: "SOL-USD",  color: "#9945ff", maxLeverage: 20 },
  HYPE: { name: "HYPE-USD", color: "#50d2c1", maxLeverage: 20 },
};
```

7. `TopNav.tsx`: `HYPERTRADE■  SIGNALS · BRANCHES` — logo + nav links, fixed top 40px, `bg-[--bg-panel] border-b border-[--border]`
8. `BottomTicker.tsx`: `● Connected  ₿ $69,005 ▼  Ξ $2,122 ▼` — fixed bottom 28px, static placeholder
9. Layout, redirect, placeholder pages

### Acceptance
- `npm run dev` works, both routes render, TopNav highlights active route, tokens applied

---

## T1 — Signal Backend

**Dependencies**: T0 (types reference only)
**Agents**: 1

### Owns
```
backend/
├── main.py
├── routers/signals.py
├── models.py
├── mock_signals.py
└── requirements.txt
```

### Tasks
1. FastAPI + CORS for localhost:3000
2. WebSocket `ws://localhost:8000/ws/signals` — tails JSONL at `/data/signals.jsonl`
3. Mock generator writes random signals every 3-8s
4. Signal schema:
```json
{
  "id": "sig_001", "timestamp": "2025-04-07T12:15:00Z",
  "asset": "BTC", "direction": "Long", "strength": 0.82,
  "type": "orderflow_imbalance",
  "meta": { "bid_volume": 12500000, "ask_volume": 8200000, "ratio": 1.52, "timeframe": "5m" }
}
```

### Acceptance
- `uvicorn main:app` starts, `wscat` receives signals

---

## T2 — Signal Frontend

**Dependencies**: T0 (shell + types), T1 (WebSocket)
**Agents**: 1

### Owns
```
frontend/
├── app/signals/page.tsx
├── components/signals/SignalFeed.tsx
├── components/signals/SignalRow.tsx
└── lib/ws.ts
```

### Tasks
1. `useSignalStream(url)` hook — ReconnectingWebSocket, returns signals + status
2. Dense data table: TIME | ASSET | DIR | STRENGTH | TYPE | BID VOL | ASK VOL | RATIO
3. New signals prepend with fade-in, max 200 in DOM, auto-scroll pause + "New signals ↓"
4. Connection status feeds BottomTicker

### Acceptance
- Real-time signal display, Hyperdash table aesthetic, auto-reconnect

---

## T3 — Margin Engine + Price Data

**Dependencies**: T0 (types)
**Agents**: 1

### Owns
```
frontend/lib/
├── margin-engine.ts
├── price-data.ts
└── data/ohlc.json
```

### Tasks
1. Bundled 180-day synthetic OHLC (genCandles from branches.jsx), swappable to real data later
2. Port all margin functions from branches.jsx verbatim:
   - `posStateAt`, `computeBranchEquity`, `computeAllBranches`, `segMetrics`, `accountStateAt`, `maintenanceMarginRate`
3. Export formatting helpers: `fmt`, `fmtK`, `fmtP`, `fmtDate`, etc.

### Acceptance
- Pure functions, no React. TypeScript compiles. Same results as branches.jsx with same inputs.

---

## T4 — Branch UI: Sidebar + Chart + Account + Tabs

**Dependencies**: T0, T3
**Agents**: 1

### Owns
```
frontend/components/branches/
├── BranchSidebar.tsx
├── EquityChart.tsx
├── AccountBar.tsx
├── BranchTabs.tsx
├── MetricsPanel.tsx
└── CompareTable.tsx
```

### Tasks
1. **BranchSidebar** (230px): branch list with color bar, icon, name, return %, metrics row. Selected = `bg-[--bg-elevated]`. Bottom: Fork + Import buttons.
2. **EquityChart** (canvas): multi-branch overlay, selected bold, others dim, fork point circles, grid lines.
3. **AccountBar**: horizontal stat strip — ACCOUNT VALUE, UNREALIZED PNL, MARGIN USED, AVAILABLE, LEVERAGE. ALL CAPS labels, mono values.
4. **BranchTabs**: POSITIONS | METRICS | COMPARE, active = `border-b-2 border-[--red-accent]`
5. **MetricsPanel**: two-column OVERVIEW + ANALYSIS grid
6. **CompareTable**: all branches, clickable rows

### Acceptance
- All render with mock data, styling matches Hyperdash

---

## T5 — Branch UI: Position Entry + Rows

**Dependencies**: T0, T3
**Agents**: 1-2

### Owns
```
frontend/components/branches/
├── PositionEntry.tsx
├── PositionRow.tsx
├── ForkConfig.tsx
└── FundDialog.tsx
```

### Tasks

1. **PositionEntry** — CRITICAL: mirrors Hyperdash order panel exactly:
   - Cross/Isolated segmented toggle
   - Leverage `10×` badge → click reveals slider (1 to asset max)
   - Long/Short equal-width buttons (green/red)
   - Asset dropdown, Margin input, Entry Price (auto from date), Entry Date, Exit Price, Exit Date
   - Computed: LIQUIDATION PRICE, ORDER VALUE, MARGIN REQUIRED, MAINTENANCE (ALL CAPS labels)
   - Action: "Add Long BTC" / "Add Short ETH" (colored by direction)

2. **PositionRow** — expandable:
   - Collapsed: asset, dir badge, mode badge, lev badge, margin, size, entry, mark, PnL, PnL%
   - Expanded: edit controls on `bg-[--bg-panel-alt]`
   - CLOSED / LIQ badges

3. **ForkConfig**: name, date, balance (auto-calc), Create Fork + Cancel
4. **FundDialog**: add/withdraw with maxWithdraw validation

### Acceptance
- PositionEntry feels like placing a trade on Hyperdash
- All computed fields update live
- Badges match Hyperdash colors

---

## T6 — Import System

**Dependencies**: T0, T3
**Agents**: 1

### Owns
```
frontend/components/branches/
├── ImportDialog.tsx
└── import-parser.ts
backend/routers/branches.py    # optional server-side validation
```

### Tasks
1. Client-side YAML/JSON parser with schema validation
2. ImportDialog: drag-and-drop + file picker, content preview, inline errors, "Import as Branch" button
3. Schema: `portfolio.name`, `portfolio.balance`, `portfolio.positions[]`

### Acceptance
- Valid files import as branches, invalid files show errors, drag-and-drop works

---

## T7 — Branch Page Assembly

**Dependencies**: T4, T5, T6
**Agents**: 1

### Owns
```
frontend/app/branches/page.tsx
```

### Tasks
1. Page-level state: `branches[]`, `selectedBranchIdx`, `activeTab`, `showFork`, `showImport`
2. `branchData = useMemo(() => computeAllBranches(branches), [branches])`
3. Compose layout: Sidebar | [EquityChart, AccountBar+PositionEntry, Tabs, TabContent]
4. Wire all callbacks (select, fork, import, add position, update, remove, fund)
5. Seed data: "Main Portfolio" with 2-3 sample positions

### Acceptance
- Full workflow: create → add positions → fork → compare → import
- Clean state management, no external libs

---

## T8 — Telegram Forwarding

**Dependencies**: T1 (JSONL protocol)
**Agents**: 1

### Owns
```
signal-daemon/
├── telegram_forwarder.py
└── requirements.txt
```

### Tasks
1. Tail JSONL, format + send via Telegram Bot API
2. Env: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TELEGRAM_ENABLED`
3. Rate limit: 1 msg / 10s. Graceful degradation.

### Acceptance
- Signals in Telegram within 2s, rate limited, disabled by default

---

## T9 — Docker Compose

**Dependencies**: All
**Agents**: 1

### Owns
```
docker-compose.yml
signal-daemon/Dockerfile
backend/Dockerfile
frontend/Dockerfile
.env.example
```

### Acceptance
- `docker compose up` starts everything, signals flow end-to-end

---

## Execution Notes

1. **Design fidelity is non-negotiable.** All agents reference the Design System section above.
2. **T0 types are the contract.** All agents import from `lib/types.ts` read-only.
3. **T3 margin math is authoritative.** Ported verbatim from branches.jsx. Don't "improve" it.
4. **T5 (Position Entry) is the hardest UI task.** Must feel like placing a trade on Hyperdash.
5. **Tab underline = `--red-accent` (#ed3602).** Not green, not white.
6. **No overengineering.** React state + context only. No Redux/Zustand.
7. **File ownership is sacred.** No two agents touch the same file.
8. **Each agent tests its own work** before reporting completion.
