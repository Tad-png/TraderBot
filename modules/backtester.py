"""Backtester — run strategies against historical data."""

import json
import os
import math
import logging
from datetime import datetime

logger = logging.getLogger('traderbot.backtest')

RESULTS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'backtest_results')


def run_backtest(bot_type, symbol, params, candles, starting_balance=10000):
    """
    Run a strategy backtest against historical candle data.

    Args:
        bot_type: 'grid', 'dca_momentum', 'mean_reversion'
        symbol: e.g. 'BTC/USDT'
        params: strategy-specific params dict
        candles: list of [timestamp, open, high, low, close, volume]
        starting_balance: paper balance to start with

    Returns dict with performance metrics.
    """
    if not candles:
        return {'error': 'No candle data provided'}

    balance = starting_balance
    positions = []  # [{side, quantity, entry_price}]
    trades = []     # [{side, quantity, entry_price, exit_price, pnl}]
    equity_curve = []
    peak_equity = starting_balance

    close_prices = [c[4] for c in candles]

    if bot_type == 'hunter' or bot_type == 'scalper':
        trades, equity_curve = _backtest_scalper(close_prices, params, starting_balance)
    elif bot_type == 'grid':
        trades, equity_curve = _backtest_grid(close_prices, params, starting_balance)
    elif bot_type == 'dca_momentum':
        trades, equity_curve = _backtest_dca(close_prices, params, starting_balance)
    elif bot_type == 'mean_reversion':
        trades, equity_curve = _backtest_mean_reversion(close_prices, params, starting_balance)
    else:
        return {'error': f'Unknown bot type: {bot_type}'}

    # Calculate metrics
    metrics = _calculate_metrics(trades, equity_curve, starting_balance)
    metrics['bot_type'] = bot_type
    metrics['symbol'] = symbol
    metrics['params'] = params
    metrics['candle_count'] = len(candles)
    metrics['start_date'] = datetime.fromtimestamp(candles[0][0] / 1000).isoformat() if candles[0][0] > 1e10 else candles[0][0]
    metrics['end_date'] = datetime.fromtimestamp(candles[-1][0] / 1000).isoformat() if candles[-1][0] > 1e10 else candles[-1][0]

    # Save results
    os.makedirs(RESULTS_DIR, exist_ok=True)
    filename = f"{bot_type}_{symbol.replace('/', '_')}_{int(datetime.now().timestamp())}.json"
    filepath = os.path.join(RESULTS_DIR, filename)
    with open(filepath, 'w') as f:
        json.dump({
            'metrics': metrics,
            'trades': trades[-100:],  # Last 100 trades
            'equity_curve': equity_curve[::max(1, len(equity_curve) // 500)],  # Downsample
        }, f, indent=2)

    metrics['results_file'] = filepath
    return metrics


def _backtest_grid(prices, params, balance):
    """Backtest grid strategy."""
    upper = params.get('upper_price', max(prices) * 0.95)
    lower = params.get('lower_price', min(prices) * 1.05)
    grid_count = params.get('grid_count', 10)
    investment = params.get('investment_amount', balance * 0.5)

    grid_size = (upper - lower) / grid_count
    qty_per_grid = (investment / grid_count) / ((upper + lower) / 2)

    grid_levels = [round(lower + i * grid_size, 8) for i in range(grid_count + 1)]
    buy_levels = set()
    sell_levels = set()

    trades = []
    equity_curve = [balance]
    open_positions = []

    # Initialize grid
    first_price = prices[0]
    for level in grid_levels:
        if level < first_price:
            buy_levels.add(level)
        elif level > first_price:
            sell_levels.add(level)

    last_price = first_price
    for price in prices[1:]:
        for level in list(grid_levels):
            # Buy signal
            if price <= level < last_price and level in buy_levels:
                cost = qty_per_grid * price * 1.001  # + fee
                if balance >= cost:
                    balance -= cost
                    open_positions.append({'entry': price, 'qty': qty_per_grid})
                    buy_levels.discard(level)
                    next_sell = round(level + grid_size, 8)
                    if next_sell <= upper:
                        sell_levels.add(next_sell)

            # Sell signal
            elif price >= level > last_price and level in sell_levels and open_positions:
                pos = open_positions.pop(0)
                proceeds = pos['qty'] * price * 0.999  # - fee
                pnl = proceeds - (pos['qty'] * pos['entry'] * 1.001)
                balance += proceeds
                trades.append({
                    'entry_price': pos['entry'], 'exit_price': price,
                    'quantity': pos['qty'], 'pnl': round(pnl, 4), 'side': 'long'
                })
                sell_levels.discard(level)
                next_buy = round(level - grid_size, 8)
                if next_buy >= lower:
                    buy_levels.add(next_buy)

        # Equity = cash + positions value
        pos_value = sum(p['qty'] * price for p in open_positions)
        equity_curve.append(round(balance + pos_value, 2))
        last_price = price

    return trades, equity_curve


def _backtest_dca(prices, params, balance):
    """Backtest DCA momentum strategy."""
    from modules.indicators import rsi as calc_rsi, ema as calc_ema

    base_amount = params.get('base_amount', balance * 0.01)
    multiplier = params.get('dca_multiplier', 1.5)
    rsi_buy = params.get('rsi_buy_threshold', 30)
    rsi_sell = params.get('rsi_sell_threshold', 70)
    take_profit = params.get('take_profit_pct', 5)
    stop_loss = params.get('stop_loss_pct', 3)

    rsi_values = calc_rsi(prices, 14)
    trades = []
    equity_curve = [balance]
    positions = []
    consecutive_dips = 0

    for i in range(1, len(prices)):
        price = prices[i]
        r = rsi_values[i]

        # Check exits
        for pos in list(positions):
            pnl_pct = ((price - pos['entry']) / pos['entry']) * 100
            if pnl_pct >= take_profit or pnl_pct <= -stop_loss:
                proceeds = pos['qty'] * price * 0.999
                pnl = proceeds - (pos['qty'] * pos['entry'] * 1.001)
                balance += proceeds
                trades.append({
                    'entry_price': pos['entry'], 'exit_price': price,
                    'quantity': pos['qty'], 'pnl': round(pnl, 4), 'side': 'long'
                })
                positions.remove(pos)

        if r is not None:
            if r < rsi_buy:
                consecutive_dips += 1
                buy_amount = base_amount * (multiplier ** (consecutive_dips - 1))
                buy_amount = min(buy_amount, balance * 0.1)
                if balance >= buy_amount * 1.001:
                    qty = buy_amount / price
                    balance -= buy_amount * 1.001
                    positions.append({'entry': price, 'qty': qty})
            elif r > rsi_sell and positions:
                for pos in positions:
                    proceeds = pos['qty'] * price * 0.999
                    pnl = proceeds - (pos['qty'] * pos['entry'] * 1.001)
                    balance += proceeds
                    trades.append({
                        'entry_price': pos['entry'], 'exit_price': price,
                        'quantity': pos['qty'], 'pnl': round(pnl, 4), 'side': 'long'
                    })
                positions.clear()
                consecutive_dips = 0
            elif r > 50:
                consecutive_dips = 0

        pos_value = sum(p['qty'] * price for p in positions)
        equity_curve.append(round(balance + pos_value, 2))

    return trades, equity_curve


def _backtest_mean_reversion(prices, params, balance):
    """Backtest mean reversion strategy."""
    from modules.indicators import bollinger_bands, rsi as calc_rsi

    bb_period = params.get('bb_period', 20)
    bb_std = params.get('bb_std', 2)
    position_size = params.get('position_size', balance * 0.05)
    take_profit = params.get('take_profit_pct', 2)
    stop_loss = params.get('stop_loss_pct', 1.5)
    max_positions = params.get('max_positions', 3)

    upper, middle, lower = bollinger_bands(prices, bb_period, bb_std)
    rsi_values = calc_rsi(prices, 14)

    trades = []
    equity_curve = [balance]
    positions = []

    for i in range(1, len(prices)):
        price = prices[i]

        # Check exits
        for pos in list(positions):
            pnl_pct = ((price - pos['entry']) / pos['entry']) * 100
            if pnl_pct >= take_profit or pnl_pct <= -stop_loss:
                proceeds = pos['qty'] * price * 0.999
                pnl = proceeds - (pos['qty'] * pos['entry'] * 1.001)
                balance += proceeds
                trades.append({
                    'entry_price': pos['entry'], 'exit_price': price,
                    'quantity': pos['qty'], 'pnl': round(pnl, 4), 'side': 'long'
                })
                positions.remove(pos)

        r = rsi_values[i] if i < len(rsi_values) else None
        l = lower[i] if i < len(lower) else None
        u = upper[i] if i < len(upper) else None

        if l is not None and r is not None and len(positions) < max_positions:
            # Buy at lower band
            if price <= l and r < 40:
                buy_amount = min(position_size, balance * 0.1)
                if balance >= buy_amount * 1.001:
                    qty = buy_amount / price
                    balance -= buy_amount * 1.001
                    positions.append({'entry': price, 'qty': qty})

        if u is not None and r is not None:
            # Sell at upper band
            if price >= u and r > 60:
                for pos in list(positions):
                    proceeds = pos['qty'] * price * 0.999
                    pnl = proceeds - (pos['qty'] * pos['entry'] * 1.001)
                    balance += proceeds
                    trades.append({
                        'entry_price': pos['entry'], 'exit_price': price,
                        'quantity': pos['qty'], 'pnl': round(pnl, 4), 'side': 'long'
                    })
                positions.clear()

        pos_value = sum(p['qty'] * price for p in positions)
        equity_curve.append(round(balance + pos_value, 2))

    return trades, equity_curve


def _backtest_scalper(prices, params, balance):
    """Backtest quick scalper strategy."""
    from modules.indicators import rsi as calc_rsi, ema as calc_ema

    trade_amount = params.get('trade_amount', balance * 0.02)
    take_profit = params.get('take_profit_pct', 0.5)
    stop_loss = params.get('stop_loss_pct', 0.3)
    max_open = params.get('max_open_trades', 3)

    rsi_values = calc_rsi(prices, 10)
    ema_short = calc_ema(prices, 5)
    ema_long = calc_ema(prices, 15)

    trades = []
    equity_curve = [balance]
    positions = []
    cooldown = 0

    for i in range(1, len(prices)):
        price = prices[i]
        cooldown = max(0, cooldown - 1)

        # Check exits
        for pos in list(positions):
            pnl_pct = ((price - pos['entry']) / pos['entry']) * 100
            if pnl_pct >= take_profit or pnl_pct <= -stop_loss:
                proceeds = pos['qty'] * price * 0.999
                pnl = proceeds - (pos['qty'] * pos['entry'] * 1.001)
                balance += proceeds
                trades.append({
                    'entry_price': pos['entry'], 'exit_price': price,
                    'quantity': pos['qty'], 'pnl': round(pnl, 4), 'side': 'long'
                })
                positions.remove(pos)

        # Entry signals
        r = rsi_values[i] if i < len(rsi_values) else None
        es = ema_short[i] if i < len(ema_short) else None
        el = ema_long[i] if i < len(ema_long) else None
        prev_es = ema_short[i-1] if i-1 < len(ema_short) else None
        prev_el = ema_long[i-1] if i-1 < len(ema_long) else None
        prev_r = rsi_values[i-1] if i-1 < len(rsi_values) else None

        if r and cooldown == 0 and len(positions) < max_open:
            rsi_bounce = prev_r and prev_r < 35 and r > prev_r
            ema_cross = (es and el and prev_es and prev_el and
                         prev_es <= prev_el and es > el)
            dip_recovery = (i >= 5 and
                            price > min(prices[i-5:i]) and
                            (price - min(prices[i-5:i])) / min(prices[i-5:i]) > 0.001 and
                            prices[i] > prices[i-1])

            signals = sum([bool(rsi_bounce), bool(ema_cross), bool(dip_recovery)])
            if signals >= 1:
                buy_amount = min(trade_amount, balance * 0.1)
                if balance >= buy_amount * 1.001:
                    qty = buy_amount / price
                    balance -= buy_amount * 1.001
                    positions.append({'entry': price, 'qty': qty})
                    cooldown = 2

        pos_value = sum(p['qty'] * price for p in positions)
        equity_curve.append(round(balance + pos_value, 2))

    return trades, equity_curve


def _calculate_metrics(trades, equity_curve, starting_balance):
    """Calculate performance metrics from backtest results."""
    if not trades:
        return {
            'total_trades': 0, 'win_rate': 0, 'profit_factor': 0,
            'max_drawdown': 0, 'sharpe_ratio': 0, 'total_return': 0,
            'final_balance': starting_balance
        }

    wins = [t for t in trades if t['pnl'] > 0]
    losses = [t for t in trades if t['pnl'] <= 0]
    gross_profit = sum(t['pnl'] for t in wins) if wins else 0
    gross_loss = abs(sum(t['pnl'] for t in losses)) if losses else 0

    win_rate = (len(wins) / len(trades) * 100) if trades else 0
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float('inf')

    # Max drawdown
    peak = starting_balance
    max_dd = 0
    for eq in equity_curve:
        if eq > peak:
            peak = eq
        dd = (peak - eq) / peak * 100
        if dd > max_dd:
            max_dd = dd

    # Sharpe ratio (simplified)
    final = equity_curve[-1] if equity_curve else starting_balance
    total_return = ((final - starting_balance) / starting_balance) * 100

    if len(equity_curve) > 1:
        returns = [(equity_curve[i] - equity_curve[i-1]) / equity_curve[i-1]
                    for i in range(1, len(equity_curve)) if equity_curve[i-1] > 0]
        if returns:
            avg_return = sum(returns) / len(returns)
            std_return = math.sqrt(sum((r - avg_return) ** 2 for r in returns) / len(returns))
            sharpe = (avg_return / std_return * math.sqrt(365)) if std_return > 0 else 0
        else:
            sharpe = 0
    else:
        sharpe = 0

    return {
        'total_trades': len(trades),
        'win_rate': round(win_rate, 1),
        'profit_factor': round(profit_factor, 2),
        'max_drawdown': round(max_dd, 2),
        'sharpe_ratio': round(sharpe, 2),
        'total_return': round(total_return, 2),
        'final_balance': round(final, 2),
        'gross_profit': round(gross_profit, 2),
        'gross_loss': round(gross_loss, 2),
        'avg_win': round(gross_profit / len(wins), 2) if wins else 0,
        'avg_loss': round(gross_loss / len(losses), 2) if losses else 0,
        'best_trade': round(max(t['pnl'] for t in trades), 2) if trades else 0,
        'worst_trade': round(min(t['pnl'] for t in trades), 2) if trades else 0,
    }
