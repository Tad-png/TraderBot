"""Paper trading engine — simulated order execution."""

import time
import random
import logging
from modules import state

logger = logging.getLogger('traderbot.paper')

# Configurable slippage (0.1% default)
SLIPPAGE_PCT = 0.001


def execute_paper_order(market, symbol, side, quantity, price, order_type='market'):
    """
    Simulate a trade execution.
    Returns a fill dict matching the format from real exchanges.
    """
    # Apply slippage for market orders
    fill_price = price
    if order_type == 'market':
        slippage = price * SLIPPAGE_PCT * random.uniform(0.5, 1.0)
        if side == 'buy':
            fill_price = price + slippage
        else:
            fill_price = price - slippage

    fill_price = round(fill_price, 8)
    cost = fill_price * quantity

    # Simulate trading fee (0.1% like Binance)
    fee_rate = 0.001
    fee = cost * fee_rate

    # Check and update paper balance
    if side == 'buy':
        total_cost = cost + fee
        current_balance = state.get_paper_balance(market)
        if current_balance < total_cost:
            return {
                'success': False,
                'error': f'Insufficient paper balance. Need ${total_cost:.2f}, have ${current_balance:.2f}'
            }
        state.update_paper_balance(market, -total_cost)
    else:
        proceeds = cost - fee
        state.update_paper_balance(market, proceeds)

    fill = {
        'success': True,
        'order_id': f'paper_{int(time.time() * 1000)}_{random.randint(1000, 9999)}',
        'market': market,
        'symbol': symbol,
        'side': side,
        'quantity': quantity,
        'price': fill_price,
        'cost': cost,
        'fee': fee,
        'is_paper': True,
        'filled_at': time.time()
    }

    logger.info(f"Paper {side} {quantity} {symbol} @ ${fill_price:.4f} (fee: ${fee:.4f})")
    return fill
