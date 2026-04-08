# Hypertrade Feature Proposals and Integration Improvements

Based on an analysis of the existing codebase (FastAPI backend, Next.js frontend, and design tokens), the following features and integrations are proposed to extend the current trading terminal capabilities:

## 1. Missing Trading Terminal Features

*   **Order Book Depth Visualization:** While there are orderflow signals and imbalance data, visualizing the raw or aggregated limit order book depth in the UI (e.g., as a histogram or depth chart) would provide immediate context for the signal convictions.
*   **Funding Rates and Liquidations Screener:** A dedicated dashboard or screener to monitor live funding rates across all supported assets (BTC, ETH, SOL, HYPE) and track real-time liquidation clusters, which often act as key support/resistance levels.
*   **Portfolio Branch Performance Screener:** A higher-level view to compare the performance metrics (Return, Drawdown, Sharpe ratio) of multiple portfolio branches simultaneously, similar to a leaderboard, rather than examining them individually.
*   **Trade History & Performance Analytics:** Detailed analytics for closed positions within a branch, including win rate, profit factor, average win/loss, and equity curve over time for individual branches.

## 2. Integration Improvements (Signals ↔ Charts ↔ Branches)

*   **Signal-to-Chart Navigation:**
    *   *Implementation:* Clicking a signal in the `SignalFeed` should deep-link to the `Charts` page.
    *   *UX Benefit:* Automatically loads the associated asset, sets the timeframe, and ideally overlays an indicator or marker on the candlestick chart showing exactly when and where the signal fired.
*   **Signal-to-Branch "One-Click Execution":**
    *   *Implementation:* Add an "Apply to Branch" action button on `SignalRow` items.
    *   *UX Benefit:* Allows users to immediately instantiate a hypothetical position in a selected Portfolio Branch based on the signal's parameters (asset, direction, recommended leverage) for backtesting its effectiveness.
*   **Chart-to-Branch Visual Trading:**
    *   *Implementation:* Integrate interaction within the QForge/PineTS chart component to allow clicking on specific price levels to simulate orders.
    *   *UX Benefit:* Users could "right-click" the chart to add a hypothetical Long/Short position to an active branch directly at that price level, making backtesting more intuitive and visual.
*   **Unified Alert System:**
    *   *Implementation:* Tie branch margin alerts (e.g., approaching liquidation in a hypothetical branch) into the same alert manager that handles signal notifications.
    *   *UX Benefit:* A single unified notification center for both trading opportunities and portfolio risk management.
