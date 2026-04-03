"""Base bot — abstract lifecycle with thread management."""

import threading
import time
import logging
from abc import ABC, abstractmethod
from modules import db, state
from modules.risk_manager import should_pause_bot
from modules.activity import log_activity, get_bot_state

logger = logging.getLogger('traderbot.bot')


class BaseBot(ABC):
    """
    Abstract base class for all trading bots.
    Subclasses must implement tick().
    """

    def __init__(self, bot_id, market, symbol, params, tick_interval=30):
        self.bot_id = bot_id
        self.market = market
        self.symbol = symbol
        self.params = params
        self.tick_interval = tick_interval
        self.status = 'stopped'  # stopped, running, paused
        self._thread = None
        self._stop_event = threading.Event()

    @abstractmethod
    def tick(self, current_price):
        """
        Called every tick_interval seconds.
        Subclasses implement their strategy logic here.
        Should return a dict with any actions taken.
        """
        pass

    def on_start(self):
        """Called once when bot starts. Override for setup."""
        pass

    def on_stop(self):
        """Called once when bot stops. Override for cleanup."""
        pass

    def start(self):
        """Start the bot in a background daemon thread."""
        if self.status == 'running':
            logger.warning(f"Bot {self.bot_id} already running")
            return

        self._stop_event.clear()
        self.status = 'running'
        db.update_bot_status(self.bot_id, 'running')

        self.on_start()

        self._thread = threading.Thread(
            target=self._run_loop,
            daemon=True,
            name=f'bot-{self.bot_id}'
        )
        self._thread.start()

        with state.bots_lock:
            state.active_bots[self.bot_id] = self

        log_activity(self.bot_id, 'started', f'Watching {self.symbol}')
        logger.info(f"Bot {self.bot_id} started ({self.__class__.__name__})")

    def pause(self):
        """Pause the bot (stops ticking but keeps state)."""
        self.status = 'paused'
        self._stop_event.set()
        db.update_bot_status(self.bot_id, 'paused')
        log_activity(self.bot_id, 'paused', 'Bot paused')
        logger.info(f"Bot {self.bot_id} paused")

    def stop(self):
        """Stop the bot and clean up."""
        self.status = 'stopped'
        self._stop_event.set()
        db.update_bot_status(self.bot_id, 'stopped')
        log_activity(self.bot_id, 'stopped', 'Bot stopped')

        with state.bots_lock:
            state.active_bots.pop(self.bot_id, None)

        self.on_stop()
        logger.info(f"Bot {self.bot_id} stopped")

    def get_status(self):
        """Get bot status dict for the dashboard."""
        trades = db.get_trades(limit=100, bot_id=self.bot_id)
        realized_pnl = sum(t['pnl'] for t in trades if t['pnl'] is not None)
        win_count = sum(1 for t in trades if t['pnl'] is not None and t['pnl'] > 0)
        total_with_pnl = sum(1 for t in trades if t['pnl'] is not None)
        positions = db.get_open_positions(bot_id=self.bot_id)

        # Get latest activity
        last = get_bot_state(self.bot_id)

        return {
            'bot_id': self.bot_id,
            'bot_type': self.__class__.__name__,
            'market': self.market,
            'symbol': self.symbol,
            'status': self.status,
            'params': self.params,
            'realized_pnl': round(realized_pnl, 2),
            'trade_count': len(trades),
            'win_rate': round(win_count / total_with_pnl * 100, 1) if total_with_pnl > 0 else 0,
            'open_positions': len(positions),
            'current_price': last.get('price') if last else None,
            'last_action': last.get('action') if last else None,
            'last_detail': last.get('details') if last else None,
            'last_time': last.get('time') if last else None,
        }

    def _run_loop(self):
        """Main bot loop. Runs in background thread."""
        from modules.data_feed import get_current_price

        logger.info(f"Bot {self.bot_id} loop started (tick every {self.tick_interval}s)")

        while not self._stop_event.is_set():
            try:
                # Check risk limits
                pause_needed, reason = should_pause_bot(self.bot_id)
                if pause_needed:
                    log_activity(self.bot_id, 'paused', f'Safety limit hit: {reason}')
                    logger.warning(f"Bot {self.bot_id} auto-paused: {reason}")
                    self.pause()
                    break

                # Get current price
                price = get_current_price(self.market, self.symbol)
                if price is None:
                    log_activity(self.bot_id, 'error', f'Could not get price for {self.symbol}')
                    self._stop_event.wait(self.tick_interval)
                    continue

                # Log that we're watching
                log_activity(self.bot_id, 'watching', f'Checking {self.symbol}', price=price)

                # Execute strategy tick
                self.tick(price)

            except Exception as e:
                logger.error(f"Bot {self.bot_id} tick error: {e}", exc_info=True)

            # Wait for next tick or stop signal
            self._stop_event.wait(self.tick_interval)

        logger.info(f"Bot {self.bot_id} loop ended")
