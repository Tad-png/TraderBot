"""Mean Reversion Bot — Bollinger Band bounce strategy."""

import logging
from modules.bots.base_bot import BaseBot
from modules.order_manager import place_order
from modules.indicators import bollinger_bands, rsi
from modules import db
from modules.activity import log_activity

logger = logging.getLogger('traderbot.bot.meanrev')


class MeanReversionBot(BaseBot):
    """
    Mean reversion using Bollinger Bands:
    - Buy when price touches or breaks below lower band (oversold bounce)
    - Sell when price touches or breaks above upper band (overbought)
    - RSI confirmation: only buy if RSI < 40, only sell if RSI > 60
    - Stop loss if price moves further against us

    Works best on stable, range-bound assets (major forex pairs, blue-chip stocks).

    Params:
        bb_period: Bollinger Band lookback (default 20)
        bb_std: standard deviations (default 2)
        position_size: $ per trade
        take_profit_pct: auto-close at this % gain (default 2)
        stop_loss_pct: auto-close at this % loss (default 1.5)
        max_positions: max simultaneous positions (default 3)
    """

    def __init__(self, bot_id, market, symbol, params, tick_interval=60):
        super().__init__(bot_id, market, symbol, params, tick_interval)
        self.bb_period = params.get('bb_period', 20)
        self.bb_std = params.get('bb_std', 2)
        self.position_size = params.get('position_size', 50)
        self.take_profit = params.get('take_profit_pct', 2)
        self.stop_loss = params.get('stop_loss_pct', 1.5)
        self.max_positions = params.get('max_positions', 3)

        self.price_history = []

    def tick(self, current_price):
        """Check Bollinger Band signals."""
        self.price_history.append(current_price)

        # Need enough data for Bollinger Bands
        min_data = max(self.bb_period + 5, 30)
        if len(self.price_history) < min_data:
            return

        # Keep history manageable
        if len(self.price_history) > 500:
            self.price_history = self.price_history[-200:]

        # Calculate indicators
        upper, middle, lower = bollinger_bands(
            self.price_history, self.bb_period, self.bb_std
        )
        rsi_values = rsi(self.price_history, period=14)

        current_upper = upper[-1]
        current_lower = lower[-1]
        current_middle = middle[-1]
        current_rsi = rsi_values[-1]

        if current_upper is None or current_rsi is None:
            return

        # Check exits first
        self._check_exits(current_price)

        # Count open positions
        positions = db.get_open_positions(bot_id=self.bot_id)
        if len(positions) >= self.max_positions:
            return

        # Buy signal: price at or below lower band + RSI confirms
        if current_price <= current_lower and current_rsi < 40:
            quantity = self.position_size / current_price
            fill = place_order(
                self.bot_id, self.market, self.symbol,
                'buy', quantity, current_price
            )
            if fill.get('success'):
                db.open_position(
                    self.bot_id, self.market, self.symbol,
                    'long', quantity, fill['price']
                )
                log_activity(self.bot_id, 'buy', f'Price hit bottom range — buying', price=current_price)
                logger.info(f"MeanRev BUY: ${current_price:.4f}")

        # Sell signal: price at or above upper band + RSI confirms
        elif current_price >= current_upper and current_rsi > 60:
            # Close any long positions
            for pos in positions:
                if pos['side'] == 'long':
                    self._close_position(pos, current_price,
                                         f"Upper band ${current_upper:.4f}, RSI={current_rsi:.1f}")

    def _check_exits(self, current_price):
        """Check take-profit and stop-loss."""
        positions = db.get_open_positions(bot_id=self.bot_id)
        for pos in positions:
            if pos['side'] == 'long':
                pnl_pct = ((current_price - pos['entry_price']) / pos['entry_price']) * 100
            else:
                pnl_pct = ((pos['entry_price'] - current_price) / pos['entry_price']) * 100

            if pnl_pct >= self.take_profit:
                self._close_position(pos, current_price, f"Take profit {pnl_pct:.1f}%")
            elif pnl_pct <= -self.stop_loss:
                self._close_position(pos, current_price, f"Stop loss {pnl_pct:.1f}%")

    def _close_position(self, pos, price, reason):
        """Close a position."""
        pnl = db.close_position(pos['id'], price)
        if pnl is not None:
            side = 'sell' if pos['side'] == 'long' else 'buy'
            fill = place_order(
                self.bot_id, self.market, self.symbol,
                side, pos['quantity'], price
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
            logger.info(f"MeanRev CLOSE: {reason}, P&L=${pnl:.4f}")
