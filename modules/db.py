"""SQLite database — schema init + all query helpers."""

import sqlite3
import os
import json
from datetime import datetime, timedelta
from modules import state

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'trading.db')


def get_conn():
    """Get a new SQLite connection (thread-safe pattern)."""
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    """Create all tables if they don't exist."""
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bot_id TEXT,
            market TEXT NOT NULL,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            order_type TEXT DEFAULT 'market',
            quantity REAL NOT NULL,
            price REAL NOT NULL,
            fee REAL DEFAULT 0,
            pnl REAL,
            is_paper INTEGER DEFAULT 1,
            status TEXT DEFAULT 'filled',
            exchange_order_id TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            closed_at TEXT
        );

        CREATE TABLE IF NOT EXISTS positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bot_id TEXT,
            market TEXT NOT NULL,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL DEFAULT 'long',
            quantity REAL NOT NULL,
            entry_price REAL NOT NULL,
            current_price REAL,
            unrealized_pnl REAL DEFAULT 0,
            is_paper INTEGER DEFAULT 1,
            opened_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS portfolio_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            total_value REAL NOT NULL,
            cash_balance REAL NOT NULL,
            positions_value REAL DEFAULT 0,
            unrealized_pnl REAL DEFAULT 0,
            realized_pnl REAL DEFAULT 0,
            is_paper INTEGER DEFAULT 1,
            snapshot_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS bot_configs (
            id TEXT PRIMARY KEY,
            bot_type TEXT NOT NULL,
            market TEXT NOT NULL,
            symbol TEXT NOT NULL,
            params TEXT DEFAULT '{}',
            status TEXT DEFAULT 'stopped',
            is_paper INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS risk_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            bot_id TEXT,
            details TEXT DEFAULT '{}',
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS backtest_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bot_type TEXT NOT NULL,
            symbol TEXT NOT NULL,
            params TEXT DEFAULT '{}',
            start_date TEXT,
            end_date TEXT,
            win_rate REAL,
            profit_factor REAL,
            max_drawdown REAL,
            sharpe_ratio REAL,
            total_return REAL,
            total_trades INTEGER,
            results_file TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_trades_bot ON trades(bot_id);
        CREATE INDEX IF NOT EXISTS idx_trades_market ON trades(market);
        CREATE INDEX IF NOT EXISTS idx_trades_created ON trades(created_at);
        CREATE INDEX IF NOT EXISTS idx_positions_bot ON positions(bot_id);
        CREATE INDEX IF NOT EXISTS idx_snapshots_time ON portfolio_snapshots(snapshot_at);
    """)
    conn.commit()
    conn.close()


# ── Trade helpers ──

def record_trade(bot_id, market, symbol, side, quantity, price, fee=0, pnl=None,
                 is_paper=1, order_type='market', exchange_order_id=None):
    """Insert a trade record. Returns the trade id."""
    with state.db_lock:
        conn = get_conn()
        cur = conn.execute(
            """INSERT INTO trades (bot_id, market, symbol, side, order_type, quantity, price,
               fee, pnl, is_paper, exchange_order_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (bot_id, market, symbol, side, order_type, quantity, price, fee, pnl,
             is_paper, exchange_order_id)
        )
        trade_id = cur.lastrowid
        conn.commit()
        conn.close()
    return trade_id


def get_trades(limit=50, offset=0, bot_id=None, market=None):
    """Get trades with optional filters."""
    conn = get_conn()
    query = "SELECT * FROM trades WHERE 1=1"
    params = []
    if bot_id:
        query += " AND bot_id = ?"
        params.append(bot_id)
    if market:
        query += " AND market = ?"
        params.append(market)
    query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_trades_since(since_dt):
    """Get all trades since a datetime string."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM trades WHERE created_at >= ? ORDER BY created_at",
        (since_dt,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Position helpers ──

def open_position(bot_id, market, symbol, side, quantity, entry_price, is_paper=1):
    """Open a new position."""
    with state.db_lock:
        conn = get_conn()
        cur = conn.execute(
            """INSERT INTO positions (bot_id, market, symbol, side, quantity, entry_price,
               current_price, is_paper) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (bot_id, market, symbol, side, quantity, entry_price, entry_price, is_paper)
        )
        pos_id = cur.lastrowid
        conn.commit()
        conn.close()
    return pos_id


def close_position(position_id, close_price):
    """Close a position and calculate realized P&L."""
    with state.db_lock:
        conn = get_conn()
        pos = conn.execute("SELECT * FROM positions WHERE id = ?", (position_id,)).fetchone()
        if not pos:
            conn.close()
            return None
        pos = dict(pos)
        if pos['side'] == 'long':
            pnl = (close_price - pos['entry_price']) * pos['quantity']
        else:
            pnl = (pos['entry_price'] - close_price) * pos['quantity']
        conn.execute("DELETE FROM positions WHERE id = ?", (position_id,))
        conn.commit()
        conn.close()
    return pnl


def get_open_positions(bot_id=None, market=None):
    """Get open positions with optional filters."""
    conn = get_conn()
    query = "SELECT * FROM positions WHERE 1=1"
    params = []
    if bot_id:
        query += " AND bot_id = ?"
        params.append(bot_id)
    if market:
        query += " AND market = ?"
        params.append(market)
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_position_price(position_id, current_price):
    """Update a position's current price and unrealized P&L."""
    with state.db_lock:
        conn = get_conn()
        pos = conn.execute("SELECT * FROM positions WHERE id = ?", (position_id,)).fetchone()
        if pos:
            pos = dict(pos)
            if pos['side'] == 'long':
                unrealized = (current_price - pos['entry_price']) * pos['quantity']
            else:
                unrealized = (pos['entry_price'] - current_price) * pos['quantity']
            conn.execute(
                "UPDATE positions SET current_price = ?, unrealized_pnl = ?, updated_at = datetime('now') WHERE id = ?",
                (current_price, unrealized, position_id)
            )
            conn.commit()
        conn.close()


# ── Portfolio snapshot helpers ──

def snapshot_portfolio(total_value, cash_balance, positions_value=0,
                       unrealized_pnl=0, realized_pnl=0, is_paper=1):
    """Record a portfolio snapshot."""
    with state.db_lock:
        conn = get_conn()
        conn.execute(
            """INSERT INTO portfolio_snapshots (total_value, cash_balance, positions_value,
               unrealized_pnl, realized_pnl, is_paper) VALUES (?, ?, ?, ?, ?, ?)""",
            (total_value, cash_balance, positions_value, unrealized_pnl, realized_pnl, is_paper)
        )
        conn.commit()
        conn.close()


def get_snapshots(period='1d', is_paper=1):
    """Get portfolio snapshots for a time period."""
    periods = {
        '1d': 1, '1w': 7, '1m': 30, '3m': 90, 'all': 3650
    }
    days = periods.get(period, 7)
    since = (datetime.utcnow() - timedelta(days=days)).isoformat()
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM portfolio_snapshots WHERE snapshot_at >= ? AND is_paper = ? ORDER BY snapshot_at",
        (since, is_paper)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Bot config helpers ──

def save_bot_config(bot_id, bot_type, market, symbol, params, is_paper=1):
    """Create or update a bot config."""
    with state.db_lock:
        conn = get_conn()
        conn.execute(
            """INSERT OR REPLACE INTO bot_configs (id, bot_type, market, symbol, params, is_paper, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, datetime('now'))""",
            (bot_id, bot_type, market, symbol, json.dumps(params), is_paper)
        )
        conn.commit()
        conn.close()


def get_bot_config(bot_id):
    """Get a single bot config."""
    conn = get_conn()
    row = conn.execute("SELECT * FROM bot_configs WHERE id = ?", (bot_id,)).fetchone()
    conn.close()
    if row:
        r = dict(row)
        r['params'] = json.loads(r['params'])
        return r
    return None


def get_all_bot_configs():
    """Get all bot configs."""
    conn = get_conn()
    rows = conn.execute("SELECT * FROM bot_configs ORDER BY created_at DESC").fetchall()
    conn.close()
    results = []
    for r in rows:
        d = dict(r)
        d['params'] = json.loads(d['params'])
        results.append(d)
    return results


def update_bot_status(bot_id, status):
    """Update a bot's status (running/paused/stopped)."""
    with state.db_lock:
        conn = get_conn()
        conn.execute(
            "UPDATE bot_configs SET status = ?, updated_at = datetime('now') WHERE id = ?",
            (status, bot_id)
        )
        conn.commit()
        conn.close()


def delete_bot_config(bot_id):
    """Delete a bot config."""
    with state.db_lock:
        conn = get_conn()
        conn.execute("DELETE FROM bot_configs WHERE id = ?", (bot_id,))
        conn.commit()
        conn.close()


# ── Risk event helpers ──

def record_risk_event(event_type, bot_id=None, details=None):
    """Log a risk event."""
    with state.db_lock:
        conn = get_conn()
        conn.execute(
            "INSERT INTO risk_events (event_type, bot_id, details) VALUES (?, ?, ?)",
            (event_type, bot_id, json.dumps(details or {}))
        )
        conn.commit()
        conn.close()


def get_risk_events(limit=50):
    """Get recent risk events."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM risk_events ORDER BY created_at DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── P&L helpers ──

def get_pnl_summary():
    """Get P&L summary: today, this week, this month, all time."""
    conn = get_conn()
    now = datetime.utcnow()
    today = now.strftime('%Y-%m-%d')
    week_ago = (now - timedelta(days=7)).isoformat()
    month_ago = (now - timedelta(days=30)).isoformat()

    def sum_pnl(since):
        row = conn.execute(
            "SELECT COALESCE(SUM(pnl), 0) as total FROM trades WHERE pnl IS NOT NULL AND created_at >= ?",
            (since,)
        ).fetchone()
        return row['total'] if row else 0

    total_row = conn.execute(
        "SELECT COALESCE(SUM(pnl), 0) as total FROM trades WHERE pnl IS NOT NULL"
    ).fetchone()

    trade_count = conn.execute("SELECT COUNT(*) as c FROM trades").fetchone()['c']
    win_count = conn.execute(
        "SELECT COUNT(*) as c FROM trades WHERE pnl IS NOT NULL AND pnl > 0"
    ).fetchone()['c']
    total_with_pnl = conn.execute(
        "SELECT COUNT(*) as c FROM trades WHERE pnl IS NOT NULL"
    ).fetchone()['c']

    today_pnl = sum_pnl(today)
    week_pnl = sum_pnl(week_ago)
    month_pnl = sum_pnl(month_ago)

    conn.close()
    return {
        'today_pnl': today_pnl,
        'week_pnl': week_pnl,
        'month_pnl': month_pnl,
        'all_time_pnl': total_row['total'] if total_row else 0,
        'total_trades': trade_count,
        'win_rate': (win_count / total_with_pnl * 100) if total_with_pnl > 0 else 0
    }
