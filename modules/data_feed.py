"""Data feed — fetch live prices and OHLCV candles across markets."""

import time
import os
import json
import logging
from datetime import datetime

logger = logging.getLogger('traderbot.data_feed')

# In-memory price cache with TTL
_price_cache = {}  # {market:symbol: (price, timestamp)}
CACHE_TTL = 10  # seconds


def get_current_price(market, symbol, exchange_instances=None):
    """Get the current price for a symbol. Uses short-lived cache to avoid hammering APIs."""
    cache_key = f"{market}:{symbol}"
    now = time.time()

    # Check cache
    if cache_key in _price_cache:
        price, cached_at = _price_cache[cache_key]
        if now - cached_at < CACHE_TTL:
            return price

    price = None

    if market == 'crypto':
        price = _fetch_crypto_price(symbol, exchange_instances)
    elif market == 'stock':
        price = _fetch_stock_price(symbol, exchange_instances)
    elif market == 'forex':
        price = _fetch_forex_price(symbol, exchange_instances)

    if price is not None:
        _price_cache[cache_key] = (price, now)

    return price


def _fetch_crypto_price(symbol, exchange_instances=None):
    """Fetch crypto price via ccxt."""
    try:
        if exchange_instances and 'crypto' in exchange_instances:
            ex = exchange_instances['crypto']
            ticker = ex.fetch_ticker(symbol)
            return ticker['last']
        # Fallback: use ccxt with default binance
        import ccxt
        ex = ccxt.binance({'enableRateLimit': True})
        ticker = ex.fetch_ticker(symbol)
        return ticker['last']
    except Exception as e:
        logger.error(f"Crypto price fetch failed for {symbol}: {e}")
        return None


def _fetch_stock_price(symbol, exchange_instances=None):
    """Fetch stock price via Alpaca."""
    try:
        if exchange_instances and 'stock' in exchange_instances:
            api = exchange_instances['stock']
            quote = api.get_latest_quote(symbol)
            return float(quote.ap) if quote.ap else float(quote.bp)
        return None
    except Exception as e:
        logger.error(f"Stock price fetch failed for {symbol}: {e}")
        return None


def _fetch_forex_price(symbol, exchange_instances=None):
    """Fetch forex price via OANDA."""
    try:
        if exchange_instances and 'forex' in exchange_instances:
            client, account_id = exchange_instances['forex']
            import oandapyV20.endpoints.pricing as pricing
            instrument = symbol.replace('/', '_')
            params = {"instruments": instrument}
            r = pricing.PricingInfo(accountID=account_id, params=params)
            client.request(r)
            prices = r.response['prices']
            if prices:
                bid = float(prices[0]['bids'][0]['price'])
                ask = float(prices[0]['asks'][0]['price'])
                return (bid + ask) / 2
        return None
    except Exception as e:
        logger.error(f"Forex price fetch failed for {symbol}: {e}")
        return None


def get_candles(market, symbol, timeframe='1h', limit=100, exchange_instances=None):
    """Fetch OHLCV candles. Returns list of [timestamp, open, high, low, close, volume]."""
    try:
        if market == 'crypto':
            return _fetch_crypto_candles(symbol, timeframe, limit, exchange_instances)
        elif market == 'stock':
            return _fetch_stock_candles(symbol, timeframe, limit, exchange_instances)
    except Exception as e:
        logger.error(f"Candle fetch failed for {market}:{symbol}: {e}")
    return []


def _fetch_crypto_candles(symbol, timeframe, limit, exchange_instances=None):
    """Fetch crypto OHLCV via ccxt."""
    try:
        if exchange_instances and 'crypto' in exchange_instances:
            ex = exchange_instances['crypto']
        else:
            import ccxt
            ex = ccxt.binance({'enableRateLimit': True})
        candles = ex.fetch_ohlcv(symbol, timeframe, limit=limit)
        return candles
    except Exception as e:
        logger.error(f"Crypto candles failed: {e}")
        return []


def _fetch_stock_candles(symbol, timeframe, limit, exchange_instances=None):
    """Fetch stock bars via Alpaca."""
    try:
        if exchange_instances and 'stock' in exchange_instances:
            api = exchange_instances['stock']
            tf_map = {'1m': '1Min', '5m': '5Min', '15m': '15Min', '1h': '1Hour', '1d': '1Day'}
            tf = tf_map.get(timeframe, '1Hour')
            bars = api.get_bars(symbol, tf, limit=limit).df
            result = []
            for idx, row in bars.iterrows():
                result.append([
                    int(idx.timestamp() * 1000),
                    row['open'], row['high'], row['low'], row['close'], row['volume']
                ])
            return result
    except Exception as e:
        logger.error(f"Stock candles failed: {e}")
    return []
