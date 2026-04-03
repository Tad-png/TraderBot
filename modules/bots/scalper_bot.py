"""Quick Scalper Bot — fast in-and-out trades on small price movements."""

import logging
from modules.bots.base_bot import BaseBot
from modules.order_manager import place_order
from modules.indicators import rsi, ema, macd
from modules import db
from modules.activity import log_activity

logger = logging.getLogger('traderbot.bot.scalper')


class ScalperBot(BaseBot):
    """
    Quick Scalper — makes fast trades on tiny price movements.

    How it works (in plain English):
    1. Watches the price every 15 seconds
    2. Looks for small dips or momentum shifts
    3. Buys quickly when it spots an opportunity
    4. Sells as soon as it makes a small profit (0.3-1%)
    5. Cuts losses fast if the trade goes wrong (0.5%)
    6. Repeats all day

    This is the most active strategy — it trades often for small gains.
    Think of it like picking up pennies, but doing it hundreds of times.

    Params:
        trade_amount: $ per trade (default 20)
        take_profit_pct: sell when up this % (default 0.5)
        stop_loss_pct: sell if down this % (default 0.3)
        max_open_trades: max trades at once (default 3)
        cooldown_ticks: wait this many ticks between trades (default 2)
    """

    def __init__(self, bot_id, market, symbol, params, tick_interval=15):
        super().__init__(bot_id, market, symbol, params, tick_interval)
        self.trade_amount = params.get('trade_amount', 20)
        self.take_profit = params.get('take_profit_pct', 0.5)
        self.stop_loss = params.get('stop_loss_pct', 0.3)
        self.max_open = params.get('max_open_trades', 3)
        self.cooldown = params.get('cooldown_ticks', 2)

        self.price_history = self._preload_prices()
        self.ticks_since_trade = 99  # start ready to trade
        self.trades_today = 0
        self.profits_today = 0

    def _preload_prices(self):
        """Pre-load 1-minute candle data for immediate indicator calculation."""
        try:
            from modules.data_feed import get_candles
            candles = get_candles(self.market, self.symbol, '1m', limit=60)
            if candles:
                prices = [c[4] for c in candles]
                log_activity(self.bot_id, 'signal',
                    f'Loaded {len(prices)} price points — scanning for trades')
                return prices
        except Exception:
            pass
        return []

    def tick(self, current_price):
        """Fast-paced scalping logic."""
        self.price_history.append(current_price)
        self.ticks_since_trade += 1

        # Need minimum data
        if len(self.price_history) < 30:
            log_activity(self.bot_id, 'waiting',
                f'Collecting data ({len(self.price_history)}/30)', price=current_price)
            return

        # Keep history lean
        if len(self.price_history) > 200:
            self.price_history = self.price_history[-100:]

        # Check exits FIRST (always)
        self._check_exits(current_price)

        # Cooldown between trades
        if self.ticks_since_trade < self.cooldown:
            return

        # Don't exceed max open trades
        positions = db.get_open_positions(bot_id=self.bot_id)
        if len(positions) >= self.max_open:
            return

        # Calculate signals
        signal = self._get_signal(current_price)

        if signal == 'buy':
            self._open_trade(current_price)
        elif signal == 'strong_buy':
            self._open_trade(current_price, multiplier=1.5)

    def _get_signal(self, current_price):
        """
        Multi-signal scalp detection:
        1. RSI oversold bounce (RSI dips below 35 then starts rising)
        2. Price momentum (short EMA crosses above long EMA)
        3. Quick dip recovery (price drops then immediately recovers)
        """
        prices = self.price_history

        # RSI signal
        rsi_values = rsi(prices, period=10)  # shorter period for faster signals
        current_rsi = rsi_values[-1]
        prev_rsi = rsi_values[-2] if len(rsi_values) > 1 else None

        if current_rsi is None or prev_rsi is None:
            return None

        # Signal 1: RSI bounce from oversold
        rsi_bounce = current_rsi > prev_rsi and current_rsi < 40 and prev_rsi < 35

        # Signal 2: Short EMA crossing above long EMA (momentum shift)
        ema_short = ema(prices, 5)
        ema_long = ema(prices, 15)
        ema_cross = (
            ema_short[-1] is not None and ema_long[-1] is not None and
            ema_short[-2] is not None and ema_long[-2] is not None and
            ema_short[-2] <= ema_long[-2] and
            ema_short[-1] > ema_long[-1]
        )

        # Signal 3: Quick dip recovery (price dropped then bounced in last few ticks)
        if len(prices) >= 5:
            recent_low = min(prices[-5:])
            dip_recovery = (
                current_price > recent_low and
                (current_price - recent_low) / recent_low > 0.001 and  # 0.1% bounce
                prices[-1] > prices[-2]  # currently going up
            )
        else:
            dip_recovery = False

        # Combine signals
        signals_triggered = sum([rsi_bounce, ema_cross, dip_recovery])

        if signals_triggered >= 2:
            log_activity(self.bot_id, 'signal',
                f'Strong signal! RSI:{current_rsi:.0f} — multiple indicators agree',
                price=current_price)
            return 'strong_buy'
        elif signals_triggered == 1:
            which = 'RSI bounce' if rsi_bounce else ('momentum shift' if ema_cross else 'dip recovery')
            log_activity(self.bot_id, 'signal',
                f'Signal: {which} (RSI:{current_rsi:.0f})',
                price=current_price)
            return 'buy'

        return None

    def _open_trade(self, price, multiplier=1.0):
        """Execute a quick scalp buy."""
        amount = self.trade_amount * multiplier
        quantity = amount / price

        fill = place_order(
            bot_id=self.bot_id,
            market=self.market,
            symbol=self.symbol,
            side='buy',
            quantity=quantity,
            price=price
        )

        if fill.get('success'):
            db.open_position(
                self.bot_id, self.market, self.symbol,
                'long', quantity, fill['price']
            )
            self.ticks_since_trade = 0
            self.trades_today += 1
            log_activity(self.bot_id, 'buy',
                f'Quick buy #{self.trades_today} — ${amount:.0f} worth',
                price=price)

    def _check_exits(self, current_price):
        """Check all positions for take-profit or stop-loss."""
        positions = db.get_open_positions(bot_id=self.bot_id)
        for pos in positions:
            pnl_pct = ((current_price - pos['entry_price']) / pos['entry_price']) * 100
            pnl_dollar = (current_price - pos['entry_price']) * pos['quantity']

            # Take profit
            if pnl_pct >= self.take_profit:
                self._close_trade(pos, current_price, pnl_dollar,
                    f'Quick profit! +{pnl_pct:.2f}% (+${pnl_dollar:.2f})')

            # Stop loss
            elif pnl_pct <= -self.stop_loss:
                self._close_trade(pos, current_price, pnl_dollar,
                    f'Cut loss: {pnl_pct:.2f}% (${pnl_dollar:.2f})')

            # Trailing — if up 0.3%+, tighten stop to breakeven
            elif pnl_pct > 0.3:
                # Update position price tracking
                db.update_position_price(pos['id'], current_price)

    def _close_trade(self, pos, price, pnl_dollar, reason):
        """Close a scalp trade."""
        pnl = db.close_position(pos['id'], price)
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
