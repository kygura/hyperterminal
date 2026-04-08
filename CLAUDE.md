# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Hypertrade is a focused trading terminal with three main features:
1. **Signal Viewer** — real-time trade signal feed from Hyperliquid + Bybit data
2. **Portfolio Branches** — backtesting engine for hypothetical portfolio scenarios
3. **Charts** — interactive candlestick chart with Pine Script indicator execution (QForge/PineTS)

Design language: cloned from hyperdash.com. See `HYPERTRADE.md` for the full design system tokens and component patterns.

## Development Commands

### Backend (Python/FastAPI)
```bash
cd backend

# Run the integrated API server (FastAPI + signal daemon as background tasks)
uv run uvicorn main:app --reload

# Run the standalone signal daemon (no HTTP server)
uv run python main_daemon.py
uv run python main_daemon.py --dry-run        # skip Telegram
uv run python main_daemon.py --log-level DEBUG
uv run python main_daemon.py inspect --asset BTC --hours 24

# Run tests
uv run pytest tests/
uv run pytest tests/test_signals_api.py        # single test file
uv run pytest tests/ -k "test_name"            # single test
```

### Frontend (Next.js)
```bash
cd frontend

bun run dev
bun run build
bun run lint        # zero warnings enforced (--max-warnings=0)
```

### Docker (full stack)
```bash
docker compose up
```

## Architecture

### Backend Structure

```
backend/
├── main.py              # re-exports main_api.app (uvicorn entrypoint)
├── main_api.py          # FastAPI app — registers routers, starts background tasks on startup
├── main_daemon.py       # All background loop coroutines live here
│                        # main_api.py imports and runs them as asyncio.create_task
├── config/
│   ├── global.yaml      # Assets, polling intervals, timeframe, alert thresholds
│   └── signals/         # Per-signal YAML configs
├── engine/
│   ├── signal_engine.py # Evaluates all signals → TradeCandidate list with conviction scoring
│   ├── watcher.py       # Hyperliquid WebSocket watcher for wallet activity
│   └── handler.py       # Handles position/order updates from watcher
├── data/
│   ├── hl_client/       # Hyperliquid REST + WebSocket clients
│   └── bybit_client.py  # Bybit REST client (OI, OHLCV, spot volume)
├── db/
│   ├── store.py         # SQLiteDataStore — all market data reads/writes
│   ├── models.py        # SQLModel ORM models (Wallet, positions)
│   └── session.py       # SQLAlchemy engine setup
├── api/
│   ├── routes.py        # Wallet/portfolio HTTP endpoints
│   ├── signals.py       # Signal history endpoints
│   ├── branches.py      # Portfolio branch CRUD + price-history endpoint
│   ├── news.py          # News polling + sentiment endpoints
│   └── telegram.py      # Telegram bot config endpoints
└── alerts.py            # AlertManager — cooldown, formatting, conviction filtering
```

**Key architectural point**: `main_daemon.py` contains all background loop coroutines (`poll_asset_contexts`, `engine_tick_loop`, `health_check_loop`, etc.). `main_api.py` imports and runs them as `asyncio.create_task` during FastAPI startup — they share the same event loop as the HTTP server.

**Signal pipeline**: HL/Bybit data pollers → `SQLiteDataStore` → `SignalEngine.evaluate_all()` → `score_confluence()` → `TradeCandidate` → `AlertManager` → Telegram/WebSocket broadcast

**Two WebSocket endpoints**:
- `ws://localhost:8000/ws` — portfolio updates (prices, wallet events)
- `ws://localhost:8000/ws/signals` — live trade signal candidates

**Signal config**: Controlled by `backend/config/global.yaml`. `strategy.timeframe` (`hourly`|`daily`|`weekly`) drives all poll intervals via `_TIMEFRAME_PROFILES` in `main_daemon.py`.

### Frontend Structure

```
frontend/
├── app/
│   ├── layout.tsx        # Root layout with TopNav + BottomTicker shell
│   ├── signals/          # Signal feed page
│   ├── branches/         # Portfolio branches page
│   └── charts/           # QForge Pine Script charting page
├── components/
│   ├── shell/            # TopNav, BottomTicker
│   ├── signals/          # SignalFeed, SignalRow
│   ├── branches/         # BranchSidebar, EquityChart, PositionEntry, etc.
│   └── charts/           # CandlestickChart, IndicatorEditor, IndicatorList
└── lib/
    ├── types.ts           # ALL shared TypeScript interfaces (Signal, Branch, Position, ASSETS map)
    ├── ws.ts              # useSignalStream hook + resolveApiUrl()
    ├── margin-engine.ts   # Pure TS margin math (posStateAt, computeAllBranches, etc.)
    ├── price-data.ts      # Bundled synthetic OHLC data; setOhlcDataset() updates from API
    └── charts.ts          # candlesToQFChart/candlesToPineTS converters; runIndicator(); localStorage indicator persistence
```

**State management**: React state + context only — no Redux/Zustand.

**`lib/types.ts` is the contract** — all components import from here; don't duplicate type definitions.

**`lib/margin-engine.ts` math is authoritative** — ported verbatim from the original branches.jsx; don't "improve" the formulas.

### Charts Feature (QForge)

The charts page (`app/charts/`) wires three libraries together:

- **`@qfo/qfchart`** — candlestick chart renderer. Instantiated imperatively (`new QFChart(container, opts)`), dynamically imported to avoid SSR. Managed via `useImperativeHandle` in `CandlestickChart.tsx` exposing `addIndicator`, `removeIndicator`, `setMarketData`.
- **`pinets`** — Pine Script runtime. `runIndicator()` in `lib/charts.ts` constructs a `PineTS` instance with candle data and calls `pine.run(code)`, returning `ctx.plots`.
- **Indicator persistence** — saved to `localStorage` under key `hypertrade:indicators` via `saveIndicator`/`loadIndicators`/`deleteIndicator` in `lib/charts.ts`.

Price data for charts is fetched from `/api/branches/price-history` and stored in the module-level `ohlcDataset` in `price-data.ts` via `setOhlcDataset()`.

### Database

SQLite at `backend/data.db`. Two separate abstractions:
- `db/store.py` (`SQLiteDataStore`) — raw market data (trades, funding, OI, OHLCV, signals)
- `db/models.py` + `db/session.py` (SQLModel/SQLAlchemy) — portfolio data (wallets, branches, positions)

### Testing

Tests use `tmp_db` fixture (function-scoped SQLite in `tmp_path`) for full isolation.

## Design System

All UI must follow the design tokens in `HYPERTRADE.md`. Key rules:
- Tab active indicator: `border-b-2 border-[--red-accent]` (`#ed3602`) — not green, not white
- Active button/tab: `border-[--red-accent] bg-[--red-accent]/10 text-[--red-accent]`
- PnL: positive = `text-[--green]` (`#38a67c`), negative = `text-[--red]` (`#bc263e`)
- Section labels: ALL CAPS, `text-xs`, `tracking-wider`, `text-[--text-secondary]`
- Data values: `font-mono tabular-nums`
- No border-radius beyond `rounded-sm`

## Environment Variables

```
TELEGRAM_BOT_TOKEN      # Optional — system works without it
TELEGRAM_CHAT_ID
BYBIT_API_KEY           # Optional — public endpoints work without auth
BYBIT_API_SECRET
PRICE_UPDATE_INTERVAL_SECONDS  # Default: 300
```
