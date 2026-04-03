"""Market Hunter Bot — scans all crypto, picks the best, trades every minute."""

import time
import logging
from modules.bots.base_bot import BaseBot
from modules.order_manager import place_order
from modules.market_scanner import scan_all_markets, get_latest_scan
from modules import db
from modules.activity import log_activity

logger = logging.getLogger('traderbot.bot.hunter')


class HunterBot(BaseBot):
    self_managed_prices = True  # We scan all coins ourselves

    """

    Every 60 seconds it:
    1. Scans 20 major crypto coins simultaneously
    2. Scores each one on 6 factors (RSI, momentum, volume, volatility, EMA, dip recovery)
    3. Picks the highest-scoring coin
    4. Buys if score is above threshold
    5. Manages all open trades with tight take-profit and stop-loss
    6. Auto-closes anything held too long

    It jumps between coins — might buy BTC one minute, SOL the next.
    Always hunting for the best opportunity across the entire market.

    Params:
        trade_amount: $ per trade (default 25)
        min_score: minimum opportunity score to trade (default 30)
        take_profit_pct: sell when up this % (default 0.2)
        stop_loss_pct: sell if down this % (default 0.25)
        max_open_trades: max concurrent trades (default 3)
        max_hold_seconds: force close after this (default 600 = 10min)
    """

    def __init__(self, bot_id, market, symbol, params, tick_interval=60):
        super().__init__(bot_id, market, symbol, params, tick_interval)
        self.trade_amount = params.get('trade_amount', 25)
        self.min_score = params.get('min_score', 30)
        self.take_profit = params.get('take_profit_pct', 0.2)
        self.stop_loss = params.get('stop_loss_pct', 0.25)
        self.max_open = params.get('max_open_trades', 3)
        self.max_hold = params.get('max_hold_seconds', 600)

        self._open_times = {}  # position_id -> timestamp
        self.total_scans = 0
        self.trades_made = 0
        self.total_profit = 0

    def tick(self, current_price):
        """
        Override the normal tick — we don't just watch one coin.
        We scan ALL coins and pick the best one.
        """
        # Check exits on all open positions first
        self._check_all_exits()

        # Scan the entire market
        self.total_scans += 1
        log_activity(self.bot_id, 'watching',
            f'Scan #{self.total_scans} — scanning 20 coins...', price=current_price)

        results = scan_all_markets(bot_id=self.bot_id)

        if not results:
            log_activity(self.bot_id, 'error', 'Scan returned no results')
            return

        # Check if we can open a new trade
        positions = db.get_open_positions(bot_id=self.bot_id)
        if len(positions) >= self.max_open:
            # Report what we're holding
            self._report_holdings(positions)
            return

        # Find the best opportunity
        best = results[0]

        if best['score'] >= self.min_score:
            self._execute_hunt(best)
        else:
            # Report top 3 but none good enough
            top3 = ', '.join(f"{r['symbol'].split('/')[0]}:{r['score']}" for r in results[:3])
            log_activity(self.bot_id, 'watching',
                f'Top scores: {top3} — need {self.min_score}+ to trade',
                price=best['price'])

    def _execute_hunt(self, opportunity):
        """Buy the best-scoring coin."""
        symbol = opportunity['symbol']
        price = opportunity['price']
        score = opportunity['score']
        quantity = self.trade_amount / price

        fill = place_order(
            bot_id=self.bot_id,
            market='crypto',
            symbol=symbol,
            side='buy',
            quantity=quantity,
            price=price
        )

        if fill.get('success'):
            pos_id = db.open_position(
                self.bot_id, 'crypto', symbol,
                'long', quantity, fill['price']
            )
            self._open_times[pos_id] = time.time()
            self.trades_made += 1

            # Build reason string
            signals = opportunity.get('signals', {})
            top_signals = [k for k, v in sorted(signals.items(), key=lambda x: -x[1]) if v > 0][:2]
            reason = ' + '.join(top_signals) if top_signals else 'multiple signals'

            log_activity(self.bot_id, 'buy',
                f'HUNT #{self.trades_made}: {symbol.split("/")[0]} — score {score}/100 ({reason}) RSI:{opportunity["rsi"]}',
                price=price)
        else:
            log_activity(self.bot_id, 'error',
                f'Buy failed for {symbol}: {fill.get("error", "unknown")}', price=price)

    def _check_all_exits(self):
        """Check all positions for exit conditions."""
        from modules.data_feed import get_current_price

        positions = db.get_open_positions(bot_id=self.bot_id)
        now = time.time()

        for pos in positions:
            # Get live price for THIS position's coin (might be different coins)
            current_price = get_current_price('crypto', pos['symbol'])
            if current_price is None:
                continue

            pnl_pct = ((current_price - pos['entry_price']) / pos['entry_price']) * 100
            pnl_dollar = (current_price - pos['entry_price']) * pos['quantity']
            opened = self._open_times.get(pos['id'], now - 60)
            held = now - opened
            coin = pos['symbol'].split('/')[0]

            # Take profit
            if pnl_pct >= self.take_profit:
                self._close_hunt(pos, current_price, pnl_dollar,
                    f'{coin} profit! +{pnl_pct:.3f}% (+${pnl_dollar:.2f}) in {held:.0f}s')

            # Stop loss
            elif pnl_pct <= -self.stop_loss:
                self._close_hunt(pos, current_price, pnl_dollar,
                    f'{coin} stop loss: {pnl_pct:.3f}% (${pnl_dollar:.2f}) after {held:.0f}s')

            # Timeout
            elif held >= self.max_hold:
                result = 'profit' if pnl_dollar > 0 else 'loss'
                self._close_hunt(pos, current_price, pnl_dollar,
                    f'{coin} timeout: {result} {pnl_pct:.3f}% (${pnl_dollar:.2f})')

            # Update tracking
            else:
                db.update_position_price(pos['id'], current_price)

    def _close_hunt(self, pos, price, pnl_dollar, reason):
        """Close a hunted trade."""
        pnl = db.close_position(pos['id'], price)
        self._open_times.pop(pos['id'], None)

        if pnl is not None:
            fill = place_order(
                self.bot_id, 'crypto', pos['symbol'],
                'sell', pos['quantity'], price
            )
            if fill.get('success') and fill.get('trade_id'):
                conn = db.get_conn()
                conn.execute("UPDATE trades SET pnl = ? WHERE id = ?",
                    (round(pnl, 4), fill['trade_id']))
                conn.commit()
                conn.close()

            self.total_profit += pnl
            action = 'profit' if pnl > 0 else 'loss'
            log_activity(self.bot_id, action, reason, price=price)

    def _report_holdings(self, positions):
        """Report current holdings while maxed out."""
        from modules.data_feed import get_current_price

        parts = []
        total_pnl = 0
        for pos in positions:
            price = get_current_price('crypto', pos['symbol'])
            if price:
                pnl_pct = ((price - pos['entry_price']) / pos['entry_price']) * 100
                coin = pos['symbol'].split('/')[0]
                emoji = '+' if pnl_pct > 0 else ''
                parts.append(f'{coin}:{emoji}{pnl_pct:.2f}%')
                total_pnl += pnl_pct

        holdings = ' | '.join(parts)
        log_activity(self.bot_id, 'watching',
            f'Full ({len(positions)}/{self.max_open}): {holdings}',
            price=None)
