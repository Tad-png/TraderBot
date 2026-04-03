"""Order manager — unified order routing through risk checks to paper or live execution."""

import logging
from modules import state, db
from modules.risk_manager import check_pre_trade
from modules.paper_engine import execute_paper_order
from modules.data_feed import get_current_price

logger = logging.getLogger('traderbot.orders')


def place_order(bot_id, market, symbol, side, quantity, price=None,
                order_type='market', exchange_instances=None):
    """
    Central order routing. Every trade flows through here.
    1. Get current price if not provided
    2. Check risk limits
    3. Route to paper or live engine
    4. Record in database
    Returns fill dict or error dict.
    """
    # Get current price if not provided
    if price is None:
        price = get_current_price(market, symbol, exchange_instances)
        if price is None:
            return {'success': False, 'error': f'Could not fetch price for {symbol}'}

    # Risk check
    allowed, reason = check_pre_trade(bot_id, market, symbol, side, quantity, price)
    if not allowed:
        logger.warning(f"Trade blocked for {bot_id}: {reason}")
        return {'success': False, 'error': reason}

    # Route to paper or live
    if state.trading_mode == 'paper':
        fill = execute_paper_order(market, symbol, side, quantity, price, order_type)
    else:
        fill = _execute_live_order(market, symbol, side, quantity, price, order_type, exchange_instances)

    if not fill.get('success'):
        return fill

    # Record trade
    is_paper = 1 if state.trading_mode == 'paper' else 0
    trade_id = db.record_trade(
        bot_id=bot_id,
        market=market,
        symbol=symbol,
        side=side,
        quantity=quantity,
        price=fill['price'],
        fee=fill.get('fee', 0),
        is_paper=is_paper,
        order_type=order_type,
        exchange_order_id=fill.get('order_id')
    )

    fill['trade_id'] = trade_id
    return fill


def _execute_live_order(market, symbol, side, quantity, price, order_type, exchange_instances):
    """Execute a live order via the appropriate exchange. Placeholder for Phase 8."""
    logger.error("Live trading not yet implemented")
    return {'success': False, 'error': 'Live trading not yet implemented. Use paper mode.'}
