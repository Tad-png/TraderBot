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

        # Separate long positions from short positions
        longs = [p for p in positions if p['side'] == 'long']
        shorts = [p for p in positions if p['side'] == 'short']

        if decision == 'BUY':
            # Close any shorts first (take profit on the short)
            for pos in shorts:
                pnl_d = (pos['entry_price'] - current_price) * pos['quantity']
                self._close_trade(pos, current_price, pnl_d,
                    f'Close short — switching to BUY ({", ".join(reasons[:2])})')
            # Open long if we have room
            if len(longs) < self.max_positions:
                self._execute_trade(current_price, 'long', confidence, reasons)
        elif decision == 'SELL':
            # Close any longs first (take profit or cut loss)
            for pos in longs:
                pnl_d = (current_price - pos['entry_price']) * pos['quantity']
                self._close_trade(pos, current_price, pnl_d,
                    f'Close long — switching to SELL ({", ".join(reasons[:2])})')
            # Open short if we have room
            if len(shorts) < self.max_positions:
                self._execute_trade(current_price, 'short', confidence, reasons)
        else:
            # HOLD
            reason_str = ', '.join(reasons[:2]) if reasons else 'no signal'
            if positions:
                total_pnl = 0
                for p in positions:
                    if p['side'] == 'long':
                        total_pnl += ((current_price - p['entry_price']) / p['entry_price']) * 100
                    else:
                        total_pnl += ((p['entry_price'] - current_price) / p['entry_price']) * 100
                log_activity(self.bot_id, 'watching',
                    f'HOLD — {reason_str} | {len(positions)} open ({total_pnl:+.3f}%)',
                    price=current_price)
            else:
                log_activity(self.bot_id, 'watching',
                    f'HOLD — {reason_str}',
                    price=current_price)

    def _analyse(self, price):
        """
        Micro-movement analysis for minute-by-minute trading.
        Focuses on what's happening RIGHT NOW, not the overall trend.
        Returns (decision, confidence 0-100, reasons[]).
        """
        prices = self.price_history
        buy_score = 0
        sell_score = 0
        reasons = []

        # 1. RSI — fast period, focused on extremes and direction
        rsi_vals = rsi(prices, 7)  # Very fast RSI
        cur_rsi = rsi_vals[-1]
        prev_rsi = rsi_vals[-2] if len(rsi_vals) > 1 else None

        if cur_rsi is not None and prev_rsi is not None:
            if cur_rsi < 30:
                buy_score += 3
                reasons.append(f'RSI oversold ({cur_rsi:.0f})')
            elif cur_rsi < 45 and cur_rsi > prev_rsi:
                buy_score += 2
                reasons.append(f'RSI turning up ({cur_rsi:.0f})')
            elif cur_rsi > 70:
                sell_score += 3
                reasons.append(f'RSI overbought ({cur_rsi:.0f})')
            elif cur_rsi > 55 and cur_rsi < prev_rsi:
                sell_score += 2
                reasons.append(f'RSI turning down ({cur_rsi:.0f})')

        # 2. Last 3 candles direction — simple and effective
        if len(prices) >= 4:
            up_count = sum(1 for i in range(-3, 0) if prices[i] > prices[i-1])
            down_count = 3 - up_count

            if up_count == 3:
                buy_score += 3
                reasons.append('3 ticks rising')
            elif up_count >= 2 and prices[-1] > prices[-2]:
                buy_score += 2
                reasons.append('price rising')
            elif down_count == 3:
                sell_score += 3
                reasons.append('3 ticks falling')
            elif down_count >= 2 and prices[-1] < prices[-2]:
                sell_score += 2
                reasons.append('price falling')

        # 3. Micro bounce / dip detection (last 5 prices)
        if len(prices) >= 6:
            window = prices[-6:]
            low_idx = window.index(min(window))
            high_idx = window.index(max(window))

            # Bouncing off a micro low
            if low_idx <= 3 and prices[-1] > prices[-2] and prices[-1] > min(window):
                pct_off_low = (prices[-1] - min(window)) / min(window) * 100
                if pct_off_low > 0.01:
                    buy_score += 2
                    reasons.append(f'bouncing +{pct_off_low:.3f}%')

            # Falling from a micro high
            if high_idx <= 3 and prices[-1] < prices[-2] and prices[-1] < max(window):
                pct_off_high = (max(window) - prices[-1]) / max(window) * 100
                if pct_off_high > 0.01:
                    sell_score += 2
                    reasons.append(f'dropping -{pct_off_high:.3f}%')

        # 4. Fast EMA direction (3 vs 8 — reacts in minutes, not hours)
        ema3 = ema(prices, 3)
        ema8 = ema(prices, 8)
        if ema3[-1] and ema8[-1]:
            if ema3[-1] > ema8[-1]:
                buy_score += 1
                if ema3[-2] and ema8[-2] and ema3[-2] <= ema8[-2]:
                    buy_score += 2
                    reasons.append('fast EMA crossed up')
            else:
                sell_score += 1
                if ema3[-2] and ema8[-2] and ema3[-2] >= ema8[-2]:
                    sell_score += 2
                    reasons.append('fast EMA crossed down')

        # 5. Spread from average — is price stretched?
        if len(prices) >= 10:
            avg_10 = sum(prices[-10:]) / 10
            spread = (price - avg_10) / avg_10 * 100
            if spread < -0.03:
                buy_score += 1
                reasons.append(f'below average ({spread:.3f}%)')
            elif spread > 0.03:
                sell_score += 1
                reasons.append(f'above average (+{spread:.3f}%)')

        # ── DECISION: 60/40 rule ──
        total = buy_score + sell_score
        if total == 0:
            return 'HOLD', 0, ['no signals']

        buy_pct = buy_score / total * 100
        sell_pct = sell_score / total * 100

        # Trade if we have 60%+ edge — that's our rule
        if buy_pct >= 60 and buy_score >= 2:
            return 'BUY', min(int(buy_pct), 95), reasons
        elif sell_pct >= 60 and sell_score >= 2:
            return 'SELL', min(int(sell_pct), 95), reasons
        else:
            return 'HOLD', 0, [f'edge: {buy_pct:.0f}/{sell_pct:.0f} — waiting']

    def _execute_trade(self, price, side, confidence, reasons):
        """Open a long or short position."""
        quantity = self.trade_amount / price
        order_side = 'buy' if side == 'long' else 'sell'

        fill = place_order(
            self.bot_id, self.market, self.symbol,
            order_side, quantity, price
        )
        if fill.get('success'):
            pos_id = db.open_position(
                self.bot_id, self.market, self.symbol,
                side, quantity, fill['price']
            )
            self._open_times[pos_id] = time.time()
            reason_str = ', '.join(reasons[:2])
            action_word = 'BUY (long)' if side == 'long' else 'SELL (short)'
            log_activity(self.bot_id, 'buy' if side == 'long' else 'sell',
                f'{action_word} — {reason_str} ({confidence}% edge)',
                price=price)

    def _check_exits(self, current_price):
        """Check take-profit, stop-loss, and timeout for both longs and shorts."""
        positions = db.get_open_positions(bot_id=self.bot_id)
        now = time.time()

        for pos in positions:
            # Calculate P&L based on direction
            if pos['side'] == 'long':
                pnl_pct = ((current_price - pos['entry_price']) / pos['entry_price']) * 100
                pnl_dollar = (current_price - pos['entry_price']) * pos['quantity']
            else:  # short
                pnl_pct = ((pos['entry_price'] - current_price) / pos['entry_price']) * 100
                pnl_dollar = (pos['entry_price'] - current_price) * pos['quantity']

            opened = self._open_times.get(pos['id'], now - 60)
            held = now - opened
            mins = held / 60
            direction = pos['side'].upper()

            if pnl_pct >= self.take_profit:
                self._close_trade(pos, current_price, pnl_dollar,
                    f'{direction} profit! +{pnl_pct:.3f}% (+${pnl_dollar:.2f}) in {mins:.0f}min')
            elif pnl_pct <= -self.stop_loss:
                self._close_trade(pos, current_price, pnl_dollar,
                    f'{direction} stop loss: {pnl_pct:.3f}% (${pnl_dollar:.2f}) after {mins:.0f}min')
            elif held >= self.max_hold:
                result = 'profit' if pnl_dollar > 0 else 'loss'
                self._close_trade(pos, current_price, pnl_dollar,
                    f'{direction} timeout {mins:.0f}min: {result} {pnl_pct:.3f}%')
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
