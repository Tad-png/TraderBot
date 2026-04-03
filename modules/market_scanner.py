"""Market Scanner — scans all major crypto markets for the best opportunity right now."""

import time
import logging
import threading
from modules.indicators import rsi, ema, macd, bollinger_bands
from modules.activity import log_activity

logger = logging.getLogger('traderbot.scanner')

# Top coins to scan (high liquidity, tradeable on all exchanges)
SCAN_SYMBOLS = [
    'BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'XRP/USDT', 'BNB/USDT',
    'DOGE/USDT', 'ADA/USDT', 'AVAX/USDT', 'DOT/USDT', 'LINK/USDT',
    'MATIC/USDT', 'UNI/USDT', 'ATOM/USDT', 'LTC/USDT', 'FIL/USDT',
    'NEAR/USDT', 'APT/USDT', 'OP/USDT', 'ARB/USDT', 'SUI/USDT',
]

# Shared scan results
_latest_scan = {
    'results': [],
    'scanned_at': 0,
    'best_pick': None,
}
_scan_lock = threading.Lock()


def get_latest_scan():
    """Get the most recent scan results."""
    with _scan_lock:
        return dict(_latest_scan)


def scan_all_markets(bot_id='hunter'):
    """
    Scan all coins and score them. Returns sorted list of opportunities.

    For each coin, we calculate:
    - RSI score (oversold = good buy, overbought = avoid)
    - Momentum score (price trending up vs down)
    - Volume spike (unusual activity = opportunity)
    - Volatility score (enough movement to profit)
    - EMA alignment (short above long = bullish)

    Combines into a single "opportunity score" from 0-100.
    """
    import ccxt

    try:
        exchange = ccxt.binance({'enableRateLimit': True})
    except Exception as e:
        logger.error(f"Scanner: exchange init failed: {e}")
        return []

    results = []
    scanned = 0

    for symbol in SCAN_SYMBOLS:
        try:
            # Fetch 1-minute candles (last 60)
            candles = exchange.fetch_ohlcv(symbol, '1m', limit=60)
            if not candles or len(candles) < 30:
                continue

            closes = [c[4] for c in candles]
            volumes = [c[5] for c in candles]
            highs = [c[2] for c in candles]
            lows = [c[3] for c in candles]

            current_price = closes[-1]
            prev_price = closes[-2]

            # 1. RSI Score (0-30 = oversold/buy opportunity)
            rsi_vals = rsi(closes, period=10)
            current_rsi = rsi_vals[-1]
            if current_rsi is None:
                continue

            rsi_score = 0
            if current_rsi < 25:
                rsi_score = 40  # Very oversold — strong buy signal
            elif current_rsi < 35:
                rsi_score = 25  # Oversold
            elif current_rsi < 45:
                rsi_score = 10  # Slightly below middle
            elif current_rsi > 75:
                rsi_score = -20  # Overbought — avoid

            # 2. Momentum Score (price direction over last 5-10 candles)
            momentum_short = (closes[-1] - closes[-3]) / closes[-3] * 100  # last 3 candles
            momentum_med = (closes[-1] - closes[-10]) / closes[-10] * 100 if len(closes) >= 10 else 0

            momentum_score = 0
            if momentum_short > 0.05 and momentum_med > 0:
                momentum_score = 20  # Rising short and medium term
            elif momentum_short > 0.02:
                momentum_score = 10  # Slight upward movement
            elif momentum_short < -0.1:
                momentum_score = -10  # Dropping — might be catching a falling knife

            # 3. Volume Score (current volume vs average)
            avg_vol = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else sum(volumes) / len(volumes)
            recent_vol = sum(volumes[-3:]) / 3
            vol_ratio = recent_vol / avg_vol if avg_vol > 0 else 1

            vol_score = 0
            if vol_ratio > 2.0:
                vol_score = 20  # Volume spike — something happening
            elif vol_ratio > 1.3:
                vol_score = 10  # Above average
            elif vol_ratio < 0.5:
                vol_score = -5  # Dead volume — hard to trade

            # 4. Volatility Score (enough price swing to profit)
            price_range = (max(highs[-10:]) - min(lows[-10:])) / current_price * 100
            vol_score_2 = 0
            if price_range > 0.3:
                vol_score_2 = 15  # Good volatility for scalping
            elif price_range > 0.15:
                vol_score_2 = 8
            elif price_range < 0.05:
                vol_score_2 = -10  # Too flat — no opportunity

            # 5. EMA Alignment (bullish = short above long)
            ema_5 = ema(closes, 5)
            ema_15 = ema(closes, 15)
            ema_score = 0
            if ema_5[-1] and ema_15[-1]:
                if ema_5[-1] > ema_15[-1]:
                    ema_score = 15  # Bullish alignment
                    # Bonus if just crossed
                    if ema_5[-2] and ema_15[-2] and ema_5[-2] <= ema_15[-2]:
                        ema_score = 25  # Fresh crossover!
                else:
                    ema_score = -5  # Bearish

            # 6. Dip recovery bonus
            recent_low = min(closes[-5:])
            dip_score = 0
            if current_price > recent_low and (current_price - recent_low) / recent_low > 0.001:
                if closes[-1] > closes[-2]:  # Recovering
                    dip_score = 10

            # Total score
            total_score = rsi_score + momentum_score + vol_score + vol_score_2 + ema_score + dip_score
            total_score = max(0, min(100, total_score))  # Clamp 0-100

            # Price change (last 5 mins)
            change_5m = ((current_price - closes[-5]) / closes[-5] * 100) if len(closes) >= 5 else 0

            results.append({
                'symbol': symbol,
                'price': current_price,
                'score': total_score,
                'rsi': round(current_rsi, 1),
                'momentum': round(momentum_short, 3),
                'volume_ratio': round(vol_ratio, 2),
                'volatility': round(price_range, 3),
                'change_5m': round(change_5m, 3),
                'signals': {
                    'rsi': rsi_score,
                    'momentum': momentum_score,
                    'volume': vol_score,
                    'volatility': vol_score_2,
                    'ema': ema_score,
                    'dip': dip_score,
                }
            })
            scanned += 1

        except Exception as e:
            logger.debug(f"Scanner: skip {symbol}: {e}")
            continue

    # Sort by score (best opportunity first)
    results.sort(key=lambda x: x['score'], reverse=True)

    # Store results
    with _scan_lock:
        _latest_scan['results'] = results
        _latest_scan['scanned_at'] = time.time()
        _latest_scan['best_pick'] = results[0] if results else None

    if results:
        best = results[0]
        log_activity(bot_id, 'signal',
            f'Scanned {scanned} coins — best: {best["symbol"]} (score {best["score"]}/100, RSI:{best["rsi"]})',
            price=best['price'])

    return results
