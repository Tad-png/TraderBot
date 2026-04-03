# CLAUDE.md — TraderBot

## What This Project Does
Multi-market automated trading platform: paper + live trading across crypto, US stocks, and forex. Flask backend with embedded HTML dashboard. Grid trading, DCA momentum, funding rate arbitrage, and mean reversion bots.

## Code Style
- Every new feature goes in the appropriate `modules/` file, or a new module if it doesn't fit.
- `api_server.py` contains **only** Flask route handlers — no business logic.
- Functions must be single-responsibility and importable independently.
- No duplicating logic that already exists in a module — import and reuse it.

## Running
```bash
pip install -r requirements.txt
python api_server.py          # starts on http://localhost:5000
```

## Key Files
- **`api_server.py`** — Thin Flask routes only.
- **`modules/config_manager.py`** — load_config() / save_config() for config.json.
- **`modules/state.py`** — Shared mutable state: trading_mode, active_bots, paper_balances.
- **`modules/db.py`** — SQLite schema + all query helpers.
- **`modules/portfolio.py`** — Portfolio value calc, snapshots, P&L breakdowns.
- **`modules/risk_manager.py`** — Position sizing (Kelly), loss limits, auto-pause.
- **`modules/order_manager.py`** — Unified order routing: paper vs live.
- **`modules/paper_engine.py`** — Simulated fills for paper trading.
- **`modules/data_feed.py`** — Price fetching + OHLCV caching.
- **`modules/indicators.py`** — RSI, EMA, Bollinger, ATR, MACD.
- **`modules/backtester.py`** — Run strategies against historical data.
- **`modules/exchanges/`** — Exchange wrappers (crypto, stocks, forex).
- **`modules/bots/`** — Bot strategies (grid, DCA, funding arb, mean reversion).

## Architecture
- **Paper mode** is default. Live requires explicit toggle.
- All bots run in daemon threads via `base_bot.py` lifecycle.
- Every trade goes through `risk_manager.check_pre_trade()` before execution.
- SQLite `trading.db` stores trades, positions, snapshots, bot configs, risk events.
- Dashboard is single-page HTML with Chart.js, served from `static/`.
