"""Funding Rate Arbitrage Bot — delta-neutral strategy collecting funding payments."""

import logging
from modules.bots.base_bot import BaseBot
from modules.order_manager import place_order
from modules import db
from modules.activity import log_activity

logger = logging.getLogger('traderbot.bot.funding')


class FundingArbBot(BaseBot):
    """
    Funding rate arbitrage:
    - When funding rate is positive (longs pay shorts):
      1. Buy on spot market (go long)
      2. Short on perpetual futures (go short)
      3. Position is delta-neutral (price moves cancel out)
      4. Collect funding payment every 8 hours
    - Close when funding rate drops below threshold

    This is one of the lowest-risk crypto strategies when executed properly.

    Params:
        position_size: $ amount to deploy
        min_funding_rate: minimum rate to enter (default 0.01% per 8h)
        exit_funding_rate: close below this rate (default 0.005%)
    """

    def __init__(self, bot_id, market, symbol, params, tick_interval=300):
        super().__init__(bot_id, market, symbol, params, tick_interval)
        self.position_size = params.get('position_size', 100)
        self.min_rate = params.get('min_funding_rate', 0.01)
        self.exit_rate = params.get('exit_funding_rate', 0.005)
        self.is_in_position = False
        self.entry_price = None
        self.total_funding_collected = 0

    def tick(self, current_price):
        """Check funding rate and manage positions."""
        funding_rate = self._get_funding_rate()

        if funding_rate is None:
            return

        if not self.is_in_position:
            # Enter position if funding rate is attractive
            if funding_rate >= self.min_rate:
                self._enter_position(current_price, funding_rate)
        else:
            # Exit if funding rate drops
            if funding_rate < self.exit_rate:
                self._exit_position(current_price, f"Funding rate dropped to {funding_rate:.4f}%")
            else:
                # Simulate collecting funding
                self._collect_funding(funding_rate)

    def _get_funding_rate(self):
        """
        Get current funding rate for the perpetual contract.
        In paper mode, simulate realistic funding rates.
        """
        import random
        # Simulate funding rate (typically 0.001% to 0.1% per 8h)
        # Positive rate = longs pay shorts (our opportunity)
        base_rate = 0.02  # 0.02% base
        noise = random.uniform(-0.015, 0.025)
        rate = base_rate + noise
        return round(rate, 4)

    def _enter_position(self, price, funding_rate):
        """Open delta-neutral position: long spot + short perp."""
        quantity = self.position_size / price

        # Buy spot
        spot_fill = place_order(
            self.bot_id, self.market, self.symbol,
            'buy', quantity, price
        )
        if not spot_fill.get('success'):
            logger.warning(f"Funding arb: spot buy failed: {spot_fill.get('error')}")
            return

        # In real trading, we'd also open a short perpetual here
        # For paper mode, we simulate the hedge
        db.open_position(
            self.bot_id, self.market, self.symbol,
            'long', quantity, price
        )

        self.is_in_position = True
        self.entry_price = price
        self.total_funding_collected = 0

        log_activity(self.bot_id, 'buy', f'Entered position — collecting {funding_rate:.4f}% every 8hrs', price=price)
        logger.info(f"Funding arb ENTERED: ${self.position_size:.2f} at ${price:.2f}")

    def _exit_position(self, price, reason):
        """Close the delta-neutral position."""
        positions = db.get_open_positions(bot_id=self.bot_id)
        for pos in positions:
            pnl = db.close_position(pos['id'], price)
            # In funding arb, the spot/perp P&L should roughly cancel out
            # The real profit is from collected funding
            fill = place_order(
                self.bot_id, self.market, self.symbol,
                'sell', pos['quantity'], price
            )
            if fill.get('success') and fill.get('trade_id'):
                # Record funding as the P&L (not spot price change)
                total_pnl = self.total_funding_collected
                conn = db.get_conn()
                conn.execute(
                    "UPDATE trades SET pnl = ? WHERE id = ?",
                    (round(total_pnl, 4), fill['trade_id'])
                )
                conn.commit()
                conn.close()

        self.is_in_position = False
        action = 'profit' if self.total_funding_collected > 0 else 'loss'
        log_activity(self.bot_id, action, f'Exited — collected ${self.total_funding_collected:.2f} in funding', price=price)
        logger.info(f"Funding arb EXITED: {reason}")

    def _collect_funding(self, funding_rate):
        """Simulate funding payment collection (every 8 hours in real life)."""
        # Pro-rate the funding based on tick interval
        # Real funding is paid every 8 hours (28800 seconds)
        hours_per_tick = self.tick_interval / 3600
        funding_per_8h = self.position_size * (funding_rate / 100)
        funding_this_tick = funding_per_8h * (hours_per_tick / 8)

        if funding_this_tick > 0:
            self.total_funding_collected += funding_this_tick
            from modules import state
            state.update_paper_balance(self.market, funding_this_tick)
