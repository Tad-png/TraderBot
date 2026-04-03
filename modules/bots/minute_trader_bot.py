"""Minute Trader Bot — sits on one coin, decides buy/sell/hold every 60 seconds."""

import time
import logging
from modules.bots.base_bot import BaseBot
from modules.order_manager import place_order
from modules.indicators import rsi, ema, macd, bollinger_bands
from modules import db
from modules.activity import log_activity

logger = logging.getLogger('traderbot.bot.minute')


class MinuteTraderBot(BaseBot):
    """
    Minute Trader — focuses on ONE coin, makes a decision every minute.

    Every 60 seconds it:
    1. Analyses the coin using 5 indicators
    2. Decides: BUY, SELL, or HOLD
    3. If BUY: opens a position (if not already maxed out)
    4. If SELL: closes positions for profit/loss
    5. Logs exactly why it made each decision

    Run one of these per coin. BTC, ETH, SOL — each gets its own trader.
    The goal: grow the balance by making smart minute-by-minute decisions.

    Params:
        trade_amount: $ per trade (default 25)
        take_profit_pct: sell when up this % (default 0.3)
        stop_loss_pct: sell if down this % (default 0.2)
        max_positions: max open at once (default 2)
        max_hold_minutes: force close after this (default 15)
    """

    def __init__(self, bot_id, market, symbol, params, tick_interval=60):
        super().__init__(bot_id, market, symbol, params, tick_interval)
        self.trade_amount = params.get('trade_amount', 25)
        self.take_profit = params.get('take_profit_pct', 0.3)
        self.stop_loss = params.get('stop_loss_pct', 0.2)
        self.max_positions = params.get('max_positions', 2)
        self.max_hold = params.get('max_hold_minutes', 15) * 60  # convert to seconds

        self.price_history = self._preload_prices()
        self._open_times = {}
        self.decisions = []  # track recent decisions for logging

    def _preload_prices(self):
        """Load recent 1-minute data for instant indicator readiness."""
        try:
            from modules.data_feed import get_candles
            candles = get_candles(self.market, self.symbol, '1m', limit=100)
            if candles:
                prices = [c[4] for c in candles]
                log_activity(self.bot_id, 'signal',
                    f'Loaded {len(prices)} minutes of {self.symbol} data — ready')
                return prices
        except Exception:
            pass
        return []

    def tick(self, current_price):
        """Every minute: analyse, decide, act."""
        self.price_history.append(current_price)

        if len(self.price_history) < 25:
            log_activity(self.bot_id, 'waiting',
                f'Warming up ({len(self.price_history)}/25)', price=current_price)
            return

        if len(self.price_history) > 300:
            self.price_history = self.price_history[-150:]

        # Always check exits first
        self._check_exits(current_price)

        # Analyse and decide
        decision, confidence, reasons = self._analyse(current_price)
        positions = db.get_open_positions(bot_id=self.bot_id)

        if decision == 'BUY' and len(positions) < self.max_positions:
            self._execute_buy(current_price, confidence, reasons)
        elif decision == 'SELL' and positions:
            self._execute_sell_all(current_price, reasons)
        else:
            # HOLD — log why
            reason_str = ', '.join(reasons[:2]) if reasons else 'no clear signal'
            action = 'watching'
            if positions:
                total_pnl = sum(
                    ((current_price - p['entry_price']) / p['entry_price']) * 100
                    for p in positions
                )
                action = 'watching'
                log_activity(self.bot_id, action,
                    f'HOLD — {reason_str} | {len(positions)} open ({total_pnl:+.3f}%)',
                    price=current_price)
            else:
                log_activity(self.bot_id, action,
                    f'HOLD — {reason_str} | watching for entry',
                    price=current_price)

    def _analyse(self, price):
        """
        Run all indicators and make a BUY/SELL/HOLD decision.
        Returns (decision, confidence 0-100, reasons[]).
        """
        prices = self.price_history
        buy_signals = 0
        sell_signals = 0
        reasons = []

        # 1. RSI
        rsi_vals = rsi(prices, 10)
        current_rsi = rsi_vals[-1]
        prev_rsi = rsi_vals[-2] if len(rsi_vals) > 1 else None

        if current_rsi is not None:
            if current_rsi < 30:
                buy_signals += 2
                reasons.append(f'RSI oversold ({current_rsi:.0f})')
            elif current_rsi < 40 and prev_rsi and current_rsi > prev_rsi:
                buy_signals += 1
                reasons.append(f'RSI recovering ({current_rsi:.0f})')
            elif current_rsi > 75:
                sell_signals += 2
                reasons.append(f'RSI overbought ({current_rsi:.0f})')
            elif current_rsi > 65:
                sell_signals += 1
                reasons.append(f'RSI high ({current_rsi:.0f})')

        # 2. EMA crossover
        ema5 = ema(prices, 5)
        ema20 = ema(prices, 20)
        if ema5[-1] and ema20[-1]:
            if ema5[-1] > ema20[-1]:
                buy_signals += 1
                if ema5[-2] and ema20[-2] and ema5[-2] <= ema20[-2]:
                    buy_signals += 2  # Fresh cross = strong
                    reasons.append('EMA just crossed up')
                else:
                    reasons.append('uptrend (EMA)')
            else:
                sell_signals += 1
                if ema5[-2] and ema20[-2] and ema5[-2] >= ema20[-2]:
                    sell_signals += 2
                    reasons.append('EMA just crossed down')
                else:
                    reasons.append('downtrend (EMA)')

        # 3. MACD
        macd_line, signal_line, histogram = macd(prices)
        if histogram[-1] is not None:
            if histogram[-1] > 0 and (histogram[-2] is None or histogram[-1] > histogram[-2]):
                buy_signals += 1
                reasons.append('MACD bullish')
            elif histogram[-1] < 0 and (histogram[-2] is None or histogram[-1] < histogram[-2]):
                sell_signals += 1
                reasons.append('MACD bearish')

        # 4. Price momentum (last 3 vs last 10 candles)
        if len(prices) >= 10:
            mom_3 = (prices[-1] - prices[-3]) / prices[-3] * 100
            mom_10 = (prices[-1] - prices[-10]) / prices[-10] * 100

            if mom_3 > 0.05 and mom_10 > 0:
                buy_signals += 1
                reasons.append(f'price rising (+{mom_3:.2f}%)')
            elif mom_3 < -0.05 and mom_10 < 0:
                sell_signals += 1
                reasons.append(f'price falling ({mom_3:.2f}%)')

        # 5. Bollinger Band position
        upper, middle, lower = bollinger_bands(prices, 20, 2)
        if lower[-1] and upper[-1]:
            band_pos = (price - lower[-1]) / (upper[-1] - lower[-1]) if upper[-1] != lower[-1] else 0.5
            if band_pos < 0.1:
                buy_signals += 2
                reasons.append('at bottom of range')
            elif band_pos < 0.3:
                buy_signals += 1
            elif band_pos > 0.9:
                sell_signals += 2
                reasons.append('at top of range')
            elif band_pos > 0.7:
                sell_signals += 1

        # Make decision
        total = buy_signals + sell_signals
        if total == 0:
            return 'HOLD', 0, ['flat market']

        if buy_signals >= 4 and buy_signals > sell_signals * 2:
            return 'BUY', min(buy_signals * 15, 95), reasons
        elif sell_signals >= 4 and sell_signals > buy_signals * 2:
            return 'SELL', min(sell_signals * 15, 95), reasons
        elif buy_signals > sell_signals + 2:
            return 'BUY', min(buy_signals * 10, 70), reasons
        elif sell_signals > buy_signals + 2:
            return 'SELL', min(sell_signals * 10, 70), reasons
        else:
            return 'HOLD', 0, reasons if reasons else ['mixed signals']

    def _execute_buy(self, price, confidence, reasons):
        """Buy with logging."""
        quantity = self.trade_amount / price
        fill = place_order(
            self.bot_id, self.market, self.symbol,
            'buy', quantity, price
        )
        if fill.get('success'):
            pos_id = db.open_position(
                self.bot_id, self.market, self.symbol,
                'long', quantity, fill['price']
            )
            self._open_times[pos_id] = time.time()
            reason_str = ', '.join(reasons[:2])
            log_activity(self.bot_id, 'buy',
                f'BUY — {reason_str} (confidence {confidence}%)',
                price=price)

    def _execute_sell_all(self, price, reasons):
        """Sell all positions."""
        positions = db.get_open_positions(bot_id=self.bot_id)
        for pos in positions:
            pnl_dollar = (price - pos['entry_price']) * pos['quantity']
            self._close_trade(pos, price, pnl_dollar,
                f'SELL signal — {", ".join(reasons[:2])}')

    def _check_exits(self, current_price):
        """Check take-profit, stop-loss, and timeout."""
        positions = db.get_open_positions(bot_id=self.bot_id)
        now = time.time()

        for pos in positions:
            pnl_pct = ((current_price - pos['entry_price']) / pos['entry_price']) * 100
            pnl_dollar = (current_price - pos['entry_price']) * pos['quantity']
            opened = self._open_times.get(pos['id'], now - 60)
            held = now - opened
            mins = held / 60

            if pnl_pct >= self.take_profit:
                self._close_trade(pos, current_price, pnl_dollar,
                    f'Profit! +{pnl_pct:.3f}% (+${pnl_dollar:.2f}) in {mins:.0f}min')
            elif pnl_pct <= -self.stop_loss:
                self._close_trade(pos, current_price, pnl_dollar,
                    f'Stop loss: {pnl_pct:.3f}% (${pnl_dollar:.2f}) after {mins:.0f}min')
            elif held >= self.max_hold:
                result = 'profit' if pnl_dollar > 0 else 'loss'
                self._close_trade(pos, current_price, pnl_dollar,
                    f'Timeout {mins:.0f}min: {result} {pnl_pct:.3f}%')
            else:
                db.update_position_price(pos['id'], current_price)

    def _close_trade(self, pos, price, pnl_dollar, reason):
        """Close a trade."""
        pnl = db.close_position(pos['id'], price)
        self._open_times.pop(pos['id'], None)
        if pnl is not None:
            fill = place_order(
                self.bot_id, self.market, self.symbol,
                'sell', pos['quantity'], price
            )
            if fill.get('success') and fill.get('trade_id'):
                conn = db.get_conn()
                conn.execute("UPDATE trades SET pnl = ? WHERE id = ?",
                    (round(pnl, 4), fill['trade_id']))
                conn.commit()
                conn.close()
            action = 'profit' if pnl > 0 else 'loss'
            log_activity(self.bot_id, action, reason, price=price)
