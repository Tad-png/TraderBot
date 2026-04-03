"""Portfolio management — value calculation, snapshots, P&L breakdowns."""

import threading
import time
import logging
from modules import state, db
from modules.data_feed import get_current_price

logger = logging.getLogger('traderbot.portfolio')

_snapshot_thread = None
_snapshot_running = False


def get_portfolio_value(exchange_instances=None):
    """Calculate total portfolio value across all markets."""
    cash = 0
    positions_value = 0
    unrealized_pnl = 0

    # Cash balance (crypto only)
    cash = state.get_paper_balance('crypto')

    # Open positions
    positions = db.get_open_positions()
    for pos in positions:
        current = get_current_price(pos['market'], pos['symbol'], exchange_instances)
        if current:
            db.update_position_price(pos['id'], current)
            value = current * pos['quantity']
            positions_value += value
            if pos['side'] == 'long':
                unrealized_pnl += (current - pos['entry_price']) * pos['quantity']
            else:
                unrealized_pnl += (pos['entry_price'] - current) * pos['quantity']

    total = cash + positions_value
    return {
        'total_value': round(total, 2),
        'cash_balance': round(cash, 2),
        'positions_value': round(positions_value, 2),
        'unrealized_pnl': round(unrealized_pnl, 2),
        'positions': positions
    }


def get_portfolio_breakdown(exchange_instances=None):
    """Get P&L breakdown by market and by bot."""
    positions = db.get_open_positions()
    by_market = {}
    by_bot = {}

    for market in ['crypto']:
        balance = state.get_paper_balance(market)
        market_positions = [p for p in positions if p['market'] == market]
        pos_value = sum(p['quantity'] * (p.get('current_price') or p['entry_price'])
                        for p in market_positions)
        by_market[market] = {
            'cash': round(balance, 2),
            'positions': round(pos_value, 2),
            'total': round(balance + pos_value, 2),
            'position_count': len(market_positions)
        }

    # By bot
    bots = db.get_all_bot_configs()
    for bot in bots:
        bot_trades = db.get_trades(limit=1000, bot_id=bot['id'])
        realized = sum(t['pnl'] for t in bot_trades if t['pnl'] is not None)
        by_bot[bot['id']] = {
            'type': bot['bot_type'],
            'symbol': bot['symbol'],
            'status': bot['status'],
            'realized_pnl': round(realized, 2),
            'trade_count': len(bot_trades)
        }

    return {'by_market': by_market, 'by_bot': by_bot}


def start_snapshot_thread(interval_minutes=5, exchange_instances=None):
    """Start background thread that takes portfolio snapshots periodically."""
    global _snapshot_thread, _snapshot_running

    if _snapshot_running:
        return

    _snapshot_running = True

    def _loop():
        while _snapshot_running:
            try:
                portfolio = get_portfolio_value(exchange_instances)
                pnl = db.get_pnl_summary()
                is_paper = 1 if state.trading_mode == 'paper' else 0
                db.snapshot_portfolio(
                    total_value=portfolio['total_value'],
                    cash_balance=portfolio['cash_balance'],
                    positions_value=portfolio['positions_value'],
                    unrealized_pnl=portfolio['unrealized_pnl'],
                    realized_pnl=pnl['all_time_pnl'],
                    is_paper=is_paper
                )
                logger.debug(f"Portfolio snapshot: ${portfolio['total_value']:.2f}")
            except Exception as e:
                logger.error(f"Snapshot error: {e}")
            time.sleep(interval_minutes * 60)

    _snapshot_thread = threading.Thread(target=_loop, daemon=True, name='snapshot')
    _snapshot_thread.start()
    logger.info(f"Portfolio snapshot thread started (every {interval_minutes}m)")


def stop_snapshot_thread():
    """Stop the snapshot thread."""
    global _snapshot_running
    _snapshot_running = False
