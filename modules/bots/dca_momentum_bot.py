"""DCA Momentum Bot — buy on RSI dips, sell on RSI highs."""

import logging
from modules.bots.base_bot import BaseBot
from modules.order_manager import place_order
from modules.indicators import rsi, ema
from modules.data_feed import get_candles
from modules import db
from modules.activity import log_activity

logger = logging.getLogger('traderbot.bot.dca')


class DCAMomentumBot(BaseBot):
    """
    DCA with momentum signals:
    - Buy when RSI drops below threshold (oversold)
    - Increase buy amount on consecutive dips (DCA multiplier)
    - Sell when RSI rises above exit threshold (overbought)
    - Uses EMA as trend filter: only buy if price is above EMA

    Params:
        base_amount: $ amount for each buy
        dca_multiplier: multiply buy amount on consecutive dips (e.g., 1.5)
        rsi_buy_threshold: buy when RSI below this (default 30)
        rsi_sell_threshold: sell when RSI above this (default 70)
        ema_period: trend filter period (default 50)
        take_profit_pct: auto-sell when up this % (default 5)
        stop_loss_pct: auto-sell when down this % (default 3)
    """

    def __init__(self, bot_id, market, symbol, params, tick_interval=60):
        super().__init__(bot_id, market, symbol, params, tick_interval)
        self.base_amount = params.get('base_amount', 10)
        self.dca_multiplier = params.get('dca_multiplier', 1.5)
        self.rsi_buy = params.get('rsi_buy_threshold', 30)
        self.rsi_sell = params.get('rsi_sell_threshold', 70)
        self.ema_period = params.get('ema_period', 50)
        self.take_profit = params.get('take_profit_pct', 5)
        self.stop_loss = params.get('stop_loss_pct', 3)

        self.price_history = self._preload_prices()
        self.consecutive_dips = 0
        self.last_rsi = None

    def _preload_prices(self):
        """Pre-load recent prices so RSI works immediately."""
        try:
            from modules.data_feed import get_candles
            candles = get_candles(self.market, self.symbol, '1m', limit=100)
            if candles:
                prices = [c[4] for c in candles]
                log_activity(self.bot_id, 'signal', f'Loaded {len(prices)} recent prices — ready to trade')
                return prices
        except Exception:
            pass
        return []

    def tick(self, current_price):
        """Check RSI signals and execute trades."""
        self.price_history.append(current_price)

        # Need at least 60 candles for reliable RSI
        if len(self.price_history) < 60:
            return

        # Calculate indicators
        rsi_values = rsi(self.price_history, period=14)
        ema_values = ema(self.price_history, self.ema_period)
        current_rsi = rsi_values[-1]
        current_ema = ema_values[-1]

        if current_rsi is None:
            return

        # Check existing positions for take profit / stop loss
        self._check_exits(current_price)

        # Buy signal: RSI below threshold
        if current_rsi < self.rsi_buy:
            # Trend filter: skip if below EMA (downtrend)
            if current_ema and current_price < current_ema * 0.97:
                logger.debug(f"DCA: RSI {current_rsi:.1f} but below EMA, skipping")
                return

            self.consecutive_dips += 1
            buy_amount = self.base_amount * (self.dca_multiplier ** (self.consecutive_dips - 1))
            quantity = buy_amount / current_price

            fill = place_order(
                bot_id=self.bot_id,
                market=self.market,
                symbol=self.symbol,
                side='buy',
                quantity=quantity,
                price=current_price
            )

            if fill.get('success'):
                db.open_position(
                    self.bot_id, self.market, self.symbol,
                    'long', quantity, fill['price']
                )
                log_activity(self.bot_id, 'buy', f'Dip #{self.consecutive_dips} — bought ${buy_amount:.2f} worth', price=current_price)
                logger.info(f"DCA BUY #{self.consecutive_dips}: ${buy_amount:.2f}")

        # Sell signal: RSI above threshold
        elif current_rsi > self.rsi_sell:
            self._sell_all_positions(current_price, f"RSI={current_rsi:.1f}")
            self.consecutive_dips = 0

        # Reset dip counter if RSI normalizes
        elif current_rsi > 50:
            self.consecutive_dips = 0

        self.last_rsi = current_rsi

    def _check_exits(self, current_price):
        """Check take-profit and stop-loss for open positions."""
        positions = db.get_open_positions(bot_id=self.bot_id)
        for pos in positions:
            pnl_pct = ((current_price - pos['entry_price']) / pos['entry_price']) * 100

            if pnl_pct >= self.take_profit:
                self._close_position(pos, current_price, f"Take profit {pnl_pct:.1f}%")
            elif pnl_pct <= -self.stop_loss:
                self._close_position(pos, current_price, f"Stop loss {pnl_pct:.1f}%")

    def _sell_all_positions(self, price, reason):
        """Close all open positions."""
        positions = db.get_open_positions(bot_id=self.bot_id)
        for pos in positions:
            self._close_position(pos, price, reason)

    def _close_position(self, pos, price, reason):
        """Close a single position."""
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
            action = 'profit' if pnl and pnl > 0 else 'loss'
            log_activity(self.bot_id, action, f'Sold — {reason}, {"+" if pnl and pnl>0 else ""}${pnl:.2f}' if pnl else f'Sold — {reason}', price=price)
            logger.info(f"DCA SELL: {reason}, P&L=${pnl:.4f}")
