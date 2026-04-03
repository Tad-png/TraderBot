"""Risk management — position sizing, loss limits, auto-pause."""

import math
import logging
from datetime import datetime, timedelta
from modules import db, state
from modules.config_manager import load_config

logger = logging.getLogger('traderbot.risk')


def check_pre_trade(bot_id, market, symbol, side, quantity, price):
    """
    Gatekeeper: checks all risk rules before allowing a trade.
    Returns (allowed: bool, reason: str).
    """
    config = load_config()
    risk = config.get('risk', {})

    # 1. Check position size vs per-trade limit
    cost = quantity * price
    total_capital = _get_total_capital(market)
    if total_capital <= 0:
        return False, "No capital available"

    max_trade_pct = risk.get('per_trade_pct', 1.5)
    max_trade_value = total_capital * (max_trade_pct / 100)
    if cost > max_trade_value:
        return False, f"Trade ${cost:.2f} exceeds {max_trade_pct}% limit (${max_trade_value:.2f})"

    # 2. Check daily loss limit
    daily_loss = _get_realized_loss_since(_today_start())
    daily_limit = total_capital * (risk.get('daily_loss_pct', 4.0) / 100)
    if abs(daily_loss) >= daily_limit:
        db.record_risk_event('daily_limit_hit', bot_id, {
            'loss': daily_loss, 'limit': daily_limit
        })
        return False, f"Daily loss limit hit: ${abs(daily_loss):.2f} / ${daily_limit:.2f}"

    # 3. Check weekly loss limit
    weekly_loss = _get_realized_loss_since(_week_start())
    weekly_limit = total_capital * (risk.get('weekly_loss_pct', 8.0) / 100)
    if abs(weekly_loss) >= weekly_limit:
        db.record_risk_event('weekly_limit_hit', bot_id, {
            'loss': weekly_loss, 'limit': weekly_limit
        })
        return False, f"Weekly loss limit hit: ${abs(weekly_loss):.2f} / ${weekly_limit:.2f}"

    return True, "OK"


def should_pause_bot(bot_id):
    """Check if a bot should be paused due to risk limits."""
    allowed, reason = check_pre_trade(bot_id, 'crypto', '', '', 0, 0)
    if not allowed and 'limit hit' in reason:
        return True, reason
    return False, ""


def calculate_position_size(capital, entry_price, stop_loss_price, win_rate=0.55, reward_risk=1.5):
    """
    Calculate position size using Quarter Kelly Criterion.
    Returns the dollar amount to risk on this trade.
    """
    config = load_config()
    risk = config.get('risk', {})
    kelly_fraction = risk.get('kelly_fraction', 0.25)

    # Kelly formula: f = (W * R - L) / R
    w = win_rate
    l = 1 - win_rate
    r = reward_risk

    kelly_pct = (w * r - l) / r
    if kelly_pct <= 0:
        return 0  # Negative expectancy — don't trade

    # Apply fraction (quarter Kelly)
    adjusted_pct = kelly_pct * kelly_fraction

    # Cap at per-trade limit
    max_pct = risk.get('per_trade_pct', 1.5) / 100
    position_pct = min(adjusted_pct, max_pct)

    dollar_risk = capital * position_pct

    # Convert to quantity based on stop distance
    if stop_loss_price and entry_price:
        risk_per_unit = abs(entry_price - stop_loss_price)
        if risk_per_unit > 0:
            quantity = dollar_risk / risk_per_unit
            return quantity

    return dollar_risk / entry_price if entry_price else 0


def get_risk_status():
    """Get current risk metrics for the dashboard."""
    config = load_config()
    risk = config.get('risk', {})
    total_capital = _get_total_capital('crypto')  # Use crypto as reference

    daily_loss = _get_realized_loss_since(_today_start())
    daily_limit = total_capital * (risk.get('daily_loss_pct', 4.0) / 100)

    weekly_loss = _get_realized_loss_since(_week_start())
    weekly_limit = total_capital * (risk.get('weekly_loss_pct', 8.0) / 100)

    # Current drawdown from peak
    snapshots = db.get_snapshots('1m')
    peak = max((s['total_value'] for s in snapshots), default=total_capital)
    current_value = total_capital
    drawdown = ((peak - current_value) / peak * 100) if peak > 0 else 0

    return {
        'daily_loss': abs(daily_loss),
        'daily_limit': daily_limit,
        'daily_pct': (abs(daily_loss) / daily_limit * 100) if daily_limit > 0 else 0,
        'weekly_loss': abs(weekly_loss),
        'weekly_limit': weekly_limit,
        'weekly_pct': (abs(weekly_loss) / weekly_limit * 100) if weekly_limit > 0 else 0,
        'drawdown_pct': drawdown,
        'per_trade_pct': risk.get('per_trade_pct', 1.5),
        'kelly_fraction': risk.get('kelly_fraction', 0.25),
    }


def _get_total_capital(market):
    """Get total capital (paper balance for now)."""
    if state.trading_mode == 'paper':
        return state.get_paper_balance(market)
    return 0  # Live mode: sum of exchange balance + positions


def _get_realized_loss_since(since_dt):
    """Sum of negative P&L trades since a datetime."""
    trades = db.get_trades_since(since_dt)
    total = sum(t['pnl'] for t in trades if t['pnl'] is not None and t['pnl'] < 0)
    return total


def _today_start():
    return datetime.utcnow().strftime('%Y-%m-%d 00:00:00')


def _week_start():
    now = datetime.utcnow()
    start = now - timedelta(days=now.weekday())
    return start.strftime('%Y-%m-%d 00:00:00')
