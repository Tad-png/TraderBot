"""Technical indicators — pure functions, no side effects."""

import math


def sma(prices, period=20):
    """Simple Moving Average. Returns list same length as prices (None-padded)."""
    result = [None] * len(prices)
    for i in range(period - 1, len(prices)):
        result[i] = sum(prices[i - period + 1:i + 1]) / period
    return result


def ema(prices, period=20):
    """Exponential Moving Average."""
    result = [None] * len(prices)
    if len(prices) < period:
        return result
    # Seed with SMA
    result[period - 1] = sum(prices[:period]) / period
    multiplier = 2 / (period + 1)
    for i in range(period, len(prices)):
        result[i] = (prices[i] - result[i - 1]) * multiplier + result[i - 1]
    return result


def rsi(prices, period=14):
    """Relative Strength Index (0-100)."""
    result = [None] * len(prices)
    if len(prices) < period + 1:
        return result

    gains = []
    losses = []
    for i in range(1, period + 1):
        change = prices[i] - prices[i - 1]
        gains.append(max(change, 0))
        losses.append(max(-change, 0))

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    if avg_loss == 0:
        result[period] = 100
    else:
        rs = avg_gain / avg_loss
        result[period] = 100 - (100 / (1 + rs))

    for i in range(period + 1, len(prices)):
        change = prices[i] - prices[i - 1]
        gain = max(change, 0)
        loss = max(-change, 0)
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        if avg_loss == 0:
            result[i] = 100
        else:
            rs = avg_gain / avg_loss
            result[i] = 100 - (100 / (1 + rs))

    return result


def bollinger_bands(prices, period=20, std_dev=2):
    """Bollinger Bands. Returns (upper, middle, lower) lists."""
    middle = sma(prices, period)
    upper = [None] * len(prices)
    lower = [None] * len(prices)

    for i in range(period - 1, len(prices)):
        window = prices[i - period + 1:i + 1]
        mean = middle[i]
        variance = sum((p - mean) ** 2 for p in window) / period
        std = math.sqrt(variance)
        upper[i] = mean + std_dev * std
        lower[i] = mean - std_dev * std

    return upper, middle, lower


def atr(highs, lows, closes, period=14):
    """Average True Range."""
    result = [None] * len(closes)
    if len(closes) < 2:
        return result

    true_ranges = [highs[0] - lows[0]]
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1])
        )
        true_ranges.append(tr)

    if len(true_ranges) < period:
        return result

    result[period - 1] = sum(true_ranges[:period]) / period
    for i in range(period, len(true_ranges)):
        result[i] = (result[i - 1] * (period - 1) + true_ranges[i]) / period

    return result


def macd(prices, fast=12, slow=26, signal_period=9):
    """MACD. Returns (macd_line, signal_line, histogram) lists."""
    fast_ema = ema(prices, fast)
    slow_ema = ema(prices, slow)

    macd_line = [None] * len(prices)
    for i in range(len(prices)):
        if fast_ema[i] is not None and slow_ema[i] is not None:
            macd_line[i] = fast_ema[i] - slow_ema[i]

    # Signal line is EMA of MACD values
    macd_values = [v for v in macd_line if v is not None]
    signal_line_values = ema(macd_values, signal_period)

    signal_line = [None] * len(prices)
    histogram = [None] * len(prices)
    j = 0
    for i in range(len(prices)):
        if macd_line[i] is not None:
            if j < len(signal_line_values) and signal_line_values[j] is not None:
                signal_line[i] = signal_line_values[j]
                histogram[i] = macd_line[i] - signal_line_values[j]
            j += 1

    return macd_line, signal_line, histogram
