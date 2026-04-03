"""Shared mutable state for the trading platform."""

import threading

# Current trading mode: "paper" or "live"
trading_mode = "paper"

# Paper trading balances per market: {"crypto": 10000, "stock": 10000, "forex": 10000}
paper_balances = {}

# Active bot instances: {bot_id: BotInstance}
active_bots = {}

# Thread locks
balance_lock = threading.Lock()
bots_lock = threading.Lock()
db_lock = threading.Lock()


def init_paper_balances(starting_balance):
    """Initialize paper balances for all markets."""
    global paper_balances
    with balance_lock:
        paper_balances = {
            "crypto": float(starting_balance),
            "stock": float(starting_balance),
            "forex": float(starting_balance),
        }


def get_paper_balance(market):
    """Get current paper balance for a market."""
    with balance_lock:
        return paper_balances.get(market, 0.0)


def update_paper_balance(market, amount):
    """Add or subtract from a market's paper balance. Returns new balance."""
    with balance_lock:
        paper_balances[market] = paper_balances.get(market, 0.0) + amount
        return paper_balances[market]
