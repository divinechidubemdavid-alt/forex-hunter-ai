import numpy as np
import pandas as pd

from config import ADX_MINIMUM
from market_data import resample_candles


def calculate_indicators(frame):
    data = frame.copy()

    close = data["close"]
    high = data["high"]
    low = data["low"]
    previous_close = close.shift(1)

    data["ema9"] = close.ewm(span=9, adjust=False).mean()
    data["ema21"] = close.ewm(span=21, adjust=False).mean()

    true_range = pd.concat(
        [
            high - low,
            (high - previous_close).abs(),
            (low - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    data["atr"] = true_range.ewm(
        alpha=1 / 14, adjust=False, min_periods=14
    ).mean()

    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = pd.Series(
        np.where((up_move > down_move) & (up_move > 0), up_move, 0.0),
        index=data.index,
    )
    minus_dm = pd.Series(
        np.where((down_move > up_move) & (down_move > 0), down_move, 0.0),
        index=data.index,
    )

    atr_smooth = true_range.ewm(
        alpha=1 / 14, adjust=False, min_periods=14
    ).mean()

    plus_di = 100 * plus_dm.ewm(
        alpha=1 / 14, adjust=False, min_periods=14
    ).mean() / atr_smooth.replace(0, np.nan)

    minus_di = 100 * minus_dm.ewm(
        alpha=1 / 14, adjust=False, min_periods=14
    ).mean() / atr_smooth.replace(0, np.nan)

    denominator = (plus_di + minus_di).replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / denominator

    data["plus_di"] = plus_di
    data["minus_di"] = minus_di
    data["adx"] = dx.ewm(
        alpha=1 / 14, adjust=False, min_periods=14
    ).mean()

    return data


def analyze(frame, timeframe="5m"):
    data = resample_candles(frame, timeframe)
    data = calculate_indicators(data).dropna()

    if len(data) < 60:
        return {
            "status": "WAIT",
            "reason": "Not enough completed candles for analysis.",
            "symbol": None,
            "timeframe": timeframe,
        }

    last = data.iloc[-1]
    previous = data.iloc[-2]

    result = {
        "status": "WAIT",
        "reason": "",
        "symbol": None,
        "timeframe": timeframe,
        "candle_time": data.index[-1].isoformat(),
        "entry": float(last["close"]),
        "stop_loss": None,
        "take_profits": [],
        "adx": float(last["adx"]),
        "ema9": float(last["ema9"]),
        "ema21": float(last["ema21"]),
        "atr": float(last["atr"]),
        "candles": data,
    }

    if last["adx"] < ADX_MINIMUM:
        result["reason"] = (
            f"Trend strength is too weak (ADX {last['adx']:.1f}; "
            f"minimum {ADX_MINIMUM:.1f})."
        )
        return result

    bullish = (
        last["ema9"] > last["ema21"]
        and previous["ema9"] > previous["ema21"]
        and last["plus_di"] > last["minus_di"]
    )

    bearish = (
        last["ema9"] < last["ema21"]
        and previous["ema9"] < previous["ema21"]
        and last["minus_di"] > last["plus_di"]
    )

    if not bullish and not bearish:
        result["reason"] = (
            "EMA alignment and directional confirmation are not both valid."
        )
        return result

    entry = float(last["close"])
    atr = float(last["atr"])

    if not np.isfinite(atr) or atr <= 0:
        result["reason"] = "ATR is not valid yet."
        return result

    direction = "BUY" if bullish else "SELL"
    risk_distance = 1.5 * atr

    if direction == "BUY":
        stop_loss = entry - risk_distance
        targets = [
            entry + risk_distance,
            entry + 2 * risk_distance,
            entry + 3 * risk_distance,
        ]
    else:
        stop_loss = entry + risk_distance
        targets = [
            entry - risk_distance,
            entry - 2 * risk_distance,
            entry - 3 * risk_distance,
        ]

    result.update({
        "status": direction,
        "reason": "EMA 9/21 aligned; ADX and directional filters confirmed.",
        "entry": entry,
        "stop_loss": float(stop_loss),
        "take_profits": [float(value) for value in targets],
    })

    return result


def format_signal(symbol, result):
    status = result["status"]
    timeframe = result["timeframe"]

    if status == "WAIT":
        return (
            f"Forex Hunter AI — {symbol} ({timeframe})\n"
            f"Status: WAIT\n"
            f"Reason: {result.get('reason', 'No confirmed setup.')}\n\n"
            "Analysis only. No trade has been executed."
        )

    targets = result["take_profits"]

    return (
        f"Forex Hunter AI — CONFIRMED {status}\n"
        f"Market: {symbol}\n"
        f"Entry timeframe: {timeframe}\n"
        f"Closed-candle entry: {result['entry']:.5f}\n"
        f"Stop loss: {result['stop_loss']:.5f}\n"
        f"TP1: {targets[0]:.5f}\n"
        f"TP2: {targets[1]:.5f}\n"
        f"TP3: {targets[2]:.5f}\n"
        f"ADX: {result['adx']:.1f}\n\n"
        "Manual analysis only. No orders are placed. "
        "Confirm spread, news, and risk before considering any trade."
    )