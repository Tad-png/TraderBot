"""Quick Scalper Bot — fast in-and-out trades on small price movements."""

import time
import logging
from modules.bots.base_bot import BaseBot
from modules.order_manager import place_order
from modules.indicators import rsi, ema
from modules import db
from modules.activity import log_activity

logger = logging.getLogger('traderbot.bot.scalper')


class ScalperBot(BaseBot):
    """
    Quick Scalper — makes fast trades on tiny price movements.

    Checks every 15 seconds. Buys on signals, sells quickly.
    Time-limited: if a trade hasn't hit profit in 5 minutes, close it.

    Params:
        trade_amount: $ per trade (default 20)
        take_profit_pct: sell when up this % (default 0.15)
        stop_loss_pct: sell if down this % (default 0.2)
        max_open_trades: max trades at once (default 2)
        max_hold_seconds: close after this many seconds (default 300 = 5min)
    """

    def __init__(self, bot_id, market, symbol, params, tick_interval=15):
        super().__init__(bot_id, market, symbol, params, tick_interval)
        self.trade_amount = params.get('trade_amount', 20)
        self.take_profit = params.get('take_profit_pct', 0.15)
        self.stop_loss = params.get('stop_loss_pct', 0.2)
        self.max_open = params.get('max_open_trades', 2)
        self.max_hold = params.get('max_hold_seconds', 300)

        self.price_history = self._preload_prices()
        self.ticks_since_trade = 99
        self.trades_today = 0
        self.profits_today = 0
        # Track when positions were opened (position_id -> timestamp)
        self._open_times = {}

    def _preload_prices(self):
        """Pre-load 1-minute candle data for immediate indicator calculation."""
        try:
            from modules.data_feed import get_candles
            candles = get_candles(self.market, self.symbol, '1m', limit=60)
            if candles:
                prices = [c[4] for c in candles]
                log_activity(self.bot_id, 'signal',
                    f'Loaded {len(prices)} prices — ready to scalp')
                return prices
        except Exception:
            pass
        return []

    def tick(self, current_price):
        """Fast-paced scalping logic."""
        self.price_history.append(current_price)
        self.ticks_since_trade += 1

        if len(self.price_history) < 20:
            log_activity(self.bot_id, 'waiting',
                f'Warming up ({len(self.price_history)}/20)', price=current_price)
            return

        if len(self.price_history) > 200:
            self.price_history = self.price_history[-100:]

        # Always check exits first
        self._check_exits(current_price)

        # Cooldown: wait at least 2 ticks between new trades
        if self.ticks_since_trade < 2:
            return

        # Don't exceed max open trades
        positions = db.get_open_positions(bot_id=self.bot_id)
        if len(positions) >= self.max_open:
            # Log what we're holding
            total_pnl = 0
            for pos in positions:
                pnl_pct = ((current_price - pos['entry_price']) / pos['entry_price']) * 100
                total_pnl += pnl_pct
            avg_pnl = total_pnl / len(positions) if positions else 0
            hold_status = 'up' if avg_pnl > 0 else 'down'
            log_activity(self.bot_id, 'watching',
                f'Holding {len(positions)} trades ({hold_status} {abs(avg_pnl):.3f}%) — waiting for exit',
                price=current_price)
            return

        # Check for entry signals
        signal, reason = self._get_signal(current_price)
        if signal:
            self._open_trade(current_price, reason)
        else:
            log_activity(self.bot_id, 'watching',
                f'No signal yet — scanning', price=current_price)

    def _get_signal(self, current_price):
        """
        Multi-signal scalp detection. Returns (signal_type, reason) or (None, None).
        """
        prices = self.price_history
        rsi_values = rsi(prices, period=10)
        current_rsi = rsi_values[-1]
        prev_rsi = rsi_values[-2] if len(rsi_values) > 1 else None

        if current_rsi is None or prev_rsi is None:
            return None, None

        # Signal 1: RSI bounce from oversold
        rsi_bounce = current_rsi > prev_rsi and current_rsi < 40 and prev_rsi < 35

        # Signal 2: Short EMA crossing above long EMA
        ema_short = ema(prices, 5)
        ema_long = ema(prices, 15)
        ema_cross = (
            ema_short[-1] is not None and ema_long[-1] is not None and
            ema_short[-2] is not None and ema_long[-2] is not None and
            ema_short[-2] <= ema_long[-2] and
            ema_short[-1] > ema_long[-1]
        )

        # Signal 3: Quick dip recovery
        if len(prices) >= 5:
            recent_low = min(prices[-5:])
            dip_recovery = (
                current_price > recent_low and
                (current_price - recent_low) / recent_low > 0.0005 and
                prices[-1] > prices[-2]
            )
        else:
            dip_recovery = False

        # Signal 4: Price momentum — 3 consecutive up ticks
        if len(prices) >= 4:
            momentum = prices[-1] > prices[-2] > prices[-3]
        else:
            momentum = False

        # Any signal triggers a trade (aggressive)
        if rsi_bounce:
            return 'buy', f'RSI bounce ({current_rsi:.0f} recovering)'
        if ema_cross:
            return 'buy', f'Momentum shift detected'
        if dip_recovery and current_rsi < 50:
            return 'buy', f'Dip recovery spotted'
        if momentum and current_rsi < 45:
            return 'buy', f'Price rising — 3 ticks up'

        return None, None

    def _open_trade(self, price, reason):
        """Execute a quick scalp buy."""
        quantity = self.trade_amount / price

        fill = place_order(
            bot_id=self.bot_id,
            market=self.market,
            symbol=self.symbol,
            side='buy',
            quantity=quantity,
            price=price
        )

        if fill.get('success'):
            pos_id = db.open_position(
                self.bot_id, self.market, self.symbol,
                'long', quantity, fill['price']
            )
            self._open_times[pos_id] = time.time()
            self.ticks_since_trade = 0
            self.trades_today += 1
            log_activity(self.bot_id, 'buy',
                f'Trade #{self.trades_today}: {reason} — ${self.trade_amount:.0f} in',
                price=price)

    def _check_exits(self, current_price):
        """Check all positions for take-profit, stop-loss, or timeout."""
        positions = db.get_open_positions(bot_id=self.bot_id)
        now = time.time()

        for pos in positions:
            pnl_pct = ((current_price - pos['entry_price']) / pos['entry_price']) * 100
            pnl_dollar = (current_price - pos['entry_price']) * pos['quantity']
            opened = self._open_times.get(pos['id'], now - 60)
            held_seconds = now - opened

            # Take profit
            if pnl_pct >= self.take_profit:
                self._close_trade(pos, current_price, pnl_dollar,
                    f'Profit! +{pnl_pct:.3f}% (+${pnl_dollar:.2f}) in {held_seconds:.0f}s')

            # Stop loss
            elif pnl_pct <= -self.stop_loss:
                self._close_trade(pos, current_price, pnl_dollar,
                    f'Stop loss: {pnl_pct:.3f}% (${pnl_dollar:.2f}) after {held_seconds:.0f}s')

            # Timeout — close after max hold time regardless
            elif held_seconds >= self.max_hold:
                result = 'tiny profit' if pnl_dollar > 0 else 'small loss'
                self._close_trade(pos, current_price, pnl_dollar,
                    f'Timeout ({self.max_hold}s): {result} {pnl_pct:.3f}% (${pnl_dollar:.2f})')

            # Update position tracking
            else:
                db.update_position_price(pos['id'], current_price)

    def _close_trade(self, pos, price, pnl_dollar, reason):
        """Close a scalp trade."""
        pnl = db.close_position(pos['id'], price)
        self._open_times.pop(pos['id'], None)

        if pnl is not None:
            fill = place_order(
                self.bot_id, self.market, self.symbol,
                'sell', pos['quantity'], price
            )
            if fill.get('success') and fill.get('trade_id'):
                conn = db.get_conn()
                conn.execute(
                    "UPDATE trades SET pnl = ? WHERE id = ?",
                    (round(pnl, 4), fill['trade_id'])
                )
                conn.commit()
                conn.close()

            self.profits_today += pnl
            action = 'profit' if pnl > 0 else 'loss'
            log_activity(self.bot_id, action, reason, price=price)
