"""Grid Trading Bot — places buy/sell orders at regular price intervals."""

import logging
from modules.bots.base_bot import BaseBot
from modules.order_manager import place_order
from modules import db
from modules.activity import log_activity

logger = logging.getLogger('traderbot.bot.grid')


class GridBot(BaseBot):
    """
    Grid trading strategy:
    - Define a price range (upper, lower) and number of grid levels
    - Place buy orders below current price at each grid level
    - When a buy fills (price drops to that level), queue a sell at the next level up
    - When a sell fills (price rises to that level), queue a buy at the next level down
    - Profit from each grid "bounce"

    Best in sideways/ranging markets. DANGEROUS in strong trends.

    Params:
        upper_price: top of grid range
        lower_price: bottom of grid range
        grid_count: number of grid levels (more = smaller profits but more trades)
        investment_amount: total $ to deploy across the grid
    """

    def __init__(self, bot_id, market, symbol, params, tick_interval=30):
        super().__init__(bot_id, market, symbol, params, tick_interval)
        self.upper = params.get('upper_price', 0)
        self.lower = params.get('lower_price', 0)
        self.grid_count = params.get('grid_count', 10)
        self.investment = params.get('investment_amount', 100)

        # Calculate grid levels
        self.grid_levels = []
        self.grid_size = 0
        self.quantity_per_grid = 0

        # Track which levels have active buy/sell orders
        self.buy_levels = set()   # levels where we want to buy
        self.sell_levels = set()  # levels where we want to sell
        self.last_price = None

    def on_start(self):
        """Set up the grid levels."""
        if self.upper <= self.lower:
            logger.error(f"Grid bot {self.bot_id}: upper must be > lower")
            return

        self.grid_size = (self.upper - self.lower) / self.grid_count
        self.grid_levels = [
            round(self.lower + i * self.grid_size, 8)
            for i in range(self.grid_count + 1)
        ]

        # Amount per grid level
        self.quantity_per_grid = (self.investment / self.grid_count) / ((self.upper + self.lower) / 2)

        logger.info(
            f"Grid bot {self.bot_id}: {self.grid_count} levels from "
            f"${self.lower:.2f} to ${self.upper:.2f}, "
            f"grid size ${self.grid_size:.2f}, "
            f"qty/level {self.quantity_per_grid:.6f}"
        )

    def tick(self, current_price):
        """Check if price has crossed any grid levels since last tick."""
        if self.last_price is None:
            # First tick: set up initial grid state
            self._initialize_grid(current_price)
            self.last_price = current_price
            return

        # Check for grid level crossings
        for level in self.grid_levels:
            # Price dropped below a buy level
            if current_price <= level < self.last_price and level in self.buy_levels:
                self._execute_grid_buy(level, current_price)

            # Price rose above a sell level
            elif current_price >= level > self.last_price and level in self.sell_levels:
                self._execute_grid_sell(level, current_price)

        self.last_price = current_price

    def _initialize_grid(self, current_price):
        """Set initial buy/sell levels based on current price."""
        self.buy_levels.clear()
        self.sell_levels.clear()

        for level in self.grid_levels:
            if level < current_price:
                self.buy_levels.add(level)
            elif level > current_price:
                self.sell_levels.add(level)

        logger.info(
            f"Grid initialized: {len(self.buy_levels)} buy levels, "
            f"{len(self.sell_levels)} sell levels"
        )

    def _execute_grid_buy(self, level, actual_price):
        """Execute a buy at a grid level, then queue sell at next level up."""
        fill = place_order(
            bot_id=self.bot_id,
            market=self.market,
            symbol=self.symbol,
            side='buy',
            quantity=self.quantity_per_grid,
            price=actual_price,
            order_type='market'
        )

        if fill.get('success'):
            # Remove from buy levels, add sell at next level up
            self.buy_levels.discard(level)
            next_level = level + self.grid_size
            if next_level <= self.upper:
                self.sell_levels.add(round(next_level, 8))

            # Open position
            db.open_position(
                bot_id=self.bot_id,
                market=self.market,
                symbol=self.symbol,
                side='long',
                quantity=self.quantity_per_grid,
                entry_price=fill['price'],
                is_paper=1
            )
            log_activity(self.bot_id, 'buy', f'Bought at ${actual_price:.2f}', price=actual_price)
            logger.info(f"Grid BUY at ${actual_price:.2f} (level ${level:.2f})")
        else:
            logger.warning(f"Grid buy failed: {fill.get('error')}")

    def _execute_grid_sell(self, level, actual_price):
        """Execute a sell at a grid level, then queue buy at next level down."""
        # Find matching position to close
        positions = db.get_open_positions(bot_id=self.bot_id)
        if not positions:
            self.sell_levels.discard(level)
            return

        pos = positions[0]  # Close oldest position
        pnl = (actual_price - pos['entry_price']) * pos['quantity']

        fill = place_order(
            bot_id=self.bot_id,
            market=self.market,
            symbol=self.symbol,
            side='sell',
            quantity=pos['quantity'],
            price=actual_price,
            order_type='market'
        )

        if fill.get('success'):
            # Close position with P&L
            db.close_position(pos['id'], actual_price)

            # Update trade with P&L
            if fill.get('trade_id'):
                conn = db.get_conn()
                conn.execute(
                    "UPDATE trades SET pnl = ? WHERE id = ?",
                    (round(pnl, 4), fill['trade_id'])
                )
                conn.commit()
                conn.close()

            # Remove from sell levels, add buy at next level down
            self.sell_levels.discard(level)
            next_level = level - self.grid_size
            if next_level >= self.lower:
                self.buy_levels.add(round(next_level, 8))

            action = 'profit' if pnl > 0 else 'loss'
            log_activity(self.bot_id, action, f'Sold at ${actual_price:.2f} — {"+" if pnl>0 else ""}${pnl:.2f}', price=actual_price)
            logger.info(f"Grid SELL at ${actual_price:.2f} (level ${level:.2f}), P&L: ${pnl:.4f}")
        else:
            logger.warning(f"Grid sell failed: {fill.get('error')}")
