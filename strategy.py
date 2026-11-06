

import numpy as np
import pandas as pd

from config import (
    ADX_MINIMUM,
    EMA_FAST,
    EMA_SLOW,
    RSI_PERIOD,
    ATR_PERIOD,
)

from market_data import resample_candles


# ============================================================
# FOREX HUNTER AI — STRATEGY ENGINE V2
# Strategy A: EMA Trend Following
# Strategy B: Breakout and Retest
# ============================================================

MINIMUM_CANDLES = 80
SUPPORT_RESISTANCE_LOOKBACK = 30
BREAKOUT_LOOKBACK = 20

MINIMUM_SCORE = 70
MAXIMUM_SCORE = 100


def _safe_float(value, default=0.0):
    try:
        result = float(value)
        return result if np.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def calculate_indicators(frame):
    """Calculate EMA, ADX, RSI, ATR and market structure."""

    required = {"open", "high", "low", "close"}

    if frame is None or not required.issubset(frame.columns):
        raise ValueError("Market data must contain OHLC columns.")

    data = frame.copy()

    for column in required:
        data[column] = pd.to_numeric(
            data[column], errors="coerce"
        )

    data = data.dropna(subset=list(required))

    close = data["close"]
    high = data["high"]
    low = data["low"]
    previous_close = close.shift(1)

    # EMA trend indicators
    data["ema9"] = close.ewm(
        span=EMA_FAST, adjust=False
    ).mean()

    data["ema21"] = close.ewm(
        span=EMA_SLOW, adjust=False
    ).mean()

    # True range and ATR
    true_range = pd.concat(
        [
            high - low,
            (high - previous_close).abs(),
            (low - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    data["atr"] = true_range.ewm(
        alpha=1 / ATR_PERIOD,
        adjust=False,
        min_periods=ATR_PERIOD,
    ).mean()

    # Directional movement and ADX
    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = pd.Series(
        np.where(
            (up_move > down_move) & (up_move > 0),
            up_move,
            0.0,
        ),
        index=data.index,
    )

    minus_dm = pd.Series(
        np.where(
            (down_move > up_move) & (down_move > 0),
            down_move,
            0.0,
        ),
        index=data.index,
    )

    atr_smooth = true_range.ewm(
        alpha=1 / 14,
        adjust=False,
        min_periods=14,
    ).mean()

    plus_di = (
        100 * plus_dm.ewm(
            alpha=1 / 14,
            adjust=False,
            min_periods=14,
        ).mean()
        / atr_smooth.replace(0, np.nan)
    )

    minus_di = (
        100 * minus_dm.ewm(
            alpha=1 / 14,
            adjust=False,
            min_periods=14,
        ).mean()
        / atr_smooth.replace(0, np.nan)
    )

    denominator = (plus_di + minus_di).replace(0, np.nan)

    dx = 100 * (plus_di - minus_di).abs() / denominator

    data["plus_di"] = plus_di
    data["minus_di"] = minus_di

    data["adx"] = dx.ewm(
        alpha=1 / 14,
        adjust=False,
        min_periods=14,
    ).mean()

    # RSI
    change = close.diff()

    gains = change.clip(lower=0)
    losses = -change.clip(upper=0)

    average_gain = gains.ewm(
        alpha=1 / RSI_PERIOD,
        adjust=False,
        min_periods=RSI_PERIOD,
    ).mean()

    average_loss = losses.ewm(
        alpha=1 / RSI_PERIOD,
        adjust=False,
        min_periods=RSI_PERIOD,
    ).mean()

    relative_strength = (
        average_gain / average_loss.replace(0, np.nan)
    )

    data["rsi"] = 100 - (
        100 / (1 + relative_strength)
    )

    rsi_fallback = pd.Series(
        np.where(average_gain > 0, 100.0, 50.0),
        index=data.index,
    )

    data["rsi"] = data["rsi"].fillna(rsi_fallback)

    # Support and resistance.
    # Shift by one candle to prevent using the current candle
    # as its own previous resistance/support.
    data["resistance"] = (
        high.shift(1)
        .rolling(SUPPORT_RESISTANCE_LOOKBACK)
        .max()
    )

    data["support"] = (
        low.shift(1)
        .rolling(SUPPORT_RESISTANCE_LOOKBACK)
        .min()
    )

    # Previous breakout range.
    data["breakout_high"] = (
        high.shift(1)
        .rolling(BREAKOUT_LOOKBACK)
        .max()
    )

    data["breakout_low"] = (
        low.shift(1)
        .rolling(BREAKOUT_LOOKBACK)
        .min()
    )

    # Candle information.
    data["candle_range"] = high - low

    data["body"] = (
        data["close"] - data["open"]
    ).abs()

    data["bullish_candle"] = (
        data["close"] > data["open"]
    )

    data["bearish_candle"] = (
        data["close"] < data["open"]
    )

    return data


def _candle_confirmation(last, direction):
    if direction == "BUY":
        return bool(
            last["close"] > last["open"]
            and last["close"] >= last["low"]
            + 0.65 * last["candle_range"]
        )

    return bool(
        last["close"] < last["open"]
        and last["close"] <= last["high"]
        - 0.65 * last["candle_range"]
    )


def _trend_setup(last, previous):
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

    if bullish:
        return "BUY"

    if bearish:
        return "SELL"

    return None


def _breakout_retest_setup(data):
    """Identify a closed-candle breakout followed by a retest."""

    if len(data) < BREAKOUT_LOOKBACK + 5:
        return None

    last = data.iloc[-1]
    previous = data.iloc[-2]

    atr = _safe_float(last["atr"])

    if atr <= 0:
        return None

    tolerance = atr * 0.25

    resistance = _safe_float(
        previous["breakout_high"]
    )

    support = _safe_float(
        previous["breakout_low"]
    )

    # Previous candle broke resistance.
    # Current candle retested and closed above it.
    bullish_breakout = (
        previous["close"] > resistance
        and last["low"] <= resistance + tolerance
        and last["close"] > resistance
        and last["close"] > last["open"]
    )

    # Previous candle broke support.
    # Current candle retested and closed below it.
    bearish_breakout = (
        previous["close"] < support
        and last["high"] >= support - tolerance
        and last["close"] < support
        and last["close"] < last["open"]
    )

    if bullish_breakout:
        return "BUY"

    if bearish_breakout:
        return "SELL"

    return None


def _calculate_score(last, previous, direction, strategy):
    """Calculate setup quality, not probability of winning."""

    score = 0
    reasons = []

    # ADX trend strength: 20 points.
    adx = _safe_float(last["adx"])

    if adx >= 35:
        score += 20
        reasons.append("Strong ADX trend")
    elif adx >= 25:
        score += 16
        reasons.append("Good ADX trend")
    elif adx >= ADX_MINIMUM:
        score += 12
        reasons.append("Acceptable ADX")
    else:
        reasons.append("Weak trend strength")

    # EMA alignment: 15 points.
    if direction == "BUY":
        ema_aligned = (
            last["ema9"] > last["ema21"]
            and last["plus_di"] > last["minus_di"]
        )
    else:
        ema_aligned = (
            last["ema9"] < last["ema21"]
            and last["minus_di"] > last["plus_di"]
        )

    if ema_aligned:
        score += 15
        reasons.append("EMA and directional alignment")

    # RSI: 10 points.
    rsi = _safe_float(last["rsi"])

    if direction == "BUY" and 50 <= rsi <= 68:
        score += 10
        reasons.append("Bullish RSI confirmation")
    elif direction == "SELL" and 32 <= rsi <= 50:
        score += 10
        reasons.append("Bearish RSI confirmation")
    elif direction == "BUY" and 68 < rsi < 75:
        score += 5
        reasons.append("RSI elevated")
    elif direction == "SELL" and 25 < rsi < 32:
        score += 5
        reasons.append("RSI depressed")

    # Closed candle confirmation: 10 points.
    if _candle_confirmation(last, direction):
        score += 10
        reasons.append("Closed candle confirmation")

    # Strategy-specific evidence: 20 points.
    if strategy == "EMA_TREND":
        if (
            previous["ema9"] > previous["ema21"]
            and last["ema9"] > last["ema21"]
            and direction == "BUY"
        ) or (
            previous["ema9"] < previous["ema21"]
            and last["ema9"] < last["ema21"]
            and direction == "SELL"
        ):
            score += 20
            reasons.append("EMA trend continuation")

    elif strategy == "BREAKOUT_RETEST":
        score += 20
        reasons.append("Breakout and retest confirmed")

    # Volatility: 10 points.
    atr = _safe_float(last["atr"])
    price = abs(_safe_float(last["close"]))

    if atr > 0 and price > 0:
        volatility_ratio = atr / price

        if volatility_ratio > 0:
            score += 10
            reasons.append("Usable market volatility")

    # Directional movement: 15 points.
    plus_di = _safe_float(last["plus_di"])
    minus_di = _safe_float(last["minus_di"])

    if direction == "BUY" and plus_di > minus_di:
        score += 15
        reasons.append("Positive directional movement")

    elif direction == "SELL" and minus_di > plus_di:
        score += 15
        reasons.append("Negative directional movement")

    return min(score, MAXIMUM_SCORE), reasons


def _technical_stop(data, direction, entry, atr):
    """Find a structural stop beyond recent market swings."""

    recent = data.iloc[-8:-1]

    if recent.empty:
        return None

    if direction == "BUY":
        swing_low = _safe_float(recent["low"].min())
        stop = min(swing_low, entry - atr * 0.5)

        if stop >= entry:
            return None

        return stop

    swing_high = _safe_float(recent["high"].max())
    stop = max(swing_high, entry + atr * 0.5)

    if stop <= entry:
        return None

    return stop


def _higher_timeframe_context(frame):
    """Assess 1H trend when sufficient data is available."""

    try:
        higher = resample_candles(frame, "1h")
        higher = calculate_indicators(higher).dropna()

        if len(higher) < 30:
            return "UNAVAILABLE"

        last = higher.iloc[-1]

        if last["ema9"] > last["ema21"]:
            return "BULLISH"

        if last["ema9"] < last["ema21"]:
            return "BEARISH"

        return "NEUTRAL"

    except Exception:
        return "UNAVAILABLE"


def analyze(frame, timeframe="15m"):
    """
    Analyze market data.

    Returns a result dictionary compatible with the
    previous bot's basic analysis interface.
    """

    data = resample_candles(frame, timeframe)
    data = calculate_indicators(data).dropna()

    result = {
        "status": "WAIT",
        "reason": "",
        "symbol": None,
        "timeframe": timeframe,
        "strategy": None,
        "score": 0,
        "score_label": "Weak",
        "entry": None,
        "stop_loss": None,
        "take_profits": [],
        "risk_distance": None,
        "risk_reward": [1, 2, 3],
        "adx": None,
        "rsi": None,
        "atr": None,
        "ema9": None,
        "ema21": None,
        "higher_timeframe": "UNAVAILABLE",
        "candles": data,
    }

    if len(data) < MINIMUM_CANDLES:
        result["reason"] = (
            "Not enough candles for reliable analysis."
        )
        return result

    last = data.iloc[-1]
    previous = data.iloc[-2]

    result.update({
        "candle_time": data.index[-1].isoformat(),
        "adx": _safe_float(last["adx"]),
        "rsi": _safe_float(last["rsi"]),
        "atr": _safe_float(last["atr"]),
        "ema9": _safe_float(last["ema9"]),
        "ema21": _safe_float(last["ema21"]),
        "higher_timeframe": _higher_timeframe_context(frame),
    })

    atr = _safe_float(last["atr"])
    entry = _safe_float(last["close"])

    if atr <= 0 or entry <= 0:
        result["reason"] = "Invalid ATR or entry price."
        return result

    if result["adx"] < ADX_MINIMUM:
        result["reason"] = (
            f"Trend strength too weak: ADX "
            f"{result['adx']:.1f}; minimum "
            f"{ADX_MINIMUM:.1f}."
        )
        return result

    # Evaluate both approved strategies.
    trend_direction = _trend_setup(last, previous)
    breakout_direction = _breakout_retest_setup(data)

    candidates = []

    if trend_direction:
        candidates.append(
            (trend_direction, "EMA_TREND")
        )

    if breakout_direction:
        candidates.append(
            (breakout_direction, "BREAKOUT_RETEST")
        )

    if not candidates:
        result["reason"] = (
            "No confirmed EMA trend or breakout-retest setup."
        )
        return result

    # Avoid conflicting strategy directions.
    directions = {item[0] for item in candidates}

    if len(directions) > 1:
        result["reason"] = (
            "The approved strategies disagree on direction."
        )
        return result

    direction = candidates[0][0]

    # If both strategies agree, use their agreement as context.
    if len(candidates) == 2:
        strategy = "EMA_TREND + BREAKOUT_RETEST"
        score_a, reasons_a = _calculate_score(
            last, previous, direction, "EMA_TREND"
        )
        score_b, reasons_b = _calculate_score(
            last, previous, direction, "BREAKOUT_RETEST"
        )

        score = min(
            100,
            max(score_a, score_b) + 5
        )

        reasons = list(dict.fromkeys(
            reasons_a + reasons_b
        ))

    else:
        direction, strategy = candidates[0]

        score, reasons = _calculate_score(
            last, previous, direction, strategy
        )

    # Higher timeframe context adjusts the score.
    context = result["higher_timeframe"]

    if (
        context == "BULLISH" and direction == "SELL"
    ) or (
        context == "BEARISH" and direction == "BUY"
    ):
        score = max(0, score - 10)
        reasons.append(
            "1H context conflicts with entry direction"
        )

    elif (
        context == "BULLISH" and direction == "BUY"
    ) or (
        context == "BEARISH" and direction == "SELL"
    ):
        score = min(100, score + 5)
        reasons.append(
            "1H context supports entry direction"
        )

    if score < MINIMUM_SCORE:
        result["score"] = score
        result["score_label"] = (
            "Weak" if score < 50
            else "Medium" if score < 70
            else "Strong"
        )
        result["reason"] = (
            f"Setup score {score}/100 is below the "
            f"70-point qualification threshold. "
            + "; ".join(reasons)
        )
        return result

    stop_loss = _technical_stop(
        data, direction, entry, atr
    )

    if stop_loss is None:
        result["reason"] = (
            "A valid structural stop-loss could not be identified."
        )
        return result

    risk_distance = abs(entry - stop_loss)

    if risk_distance <= 0:
        result["reason"] = "Invalid stop-loss distance."
        return result

    if direction == "BUY":
        targets = [
            entry + risk_distance,
            entry + 2 * risk_distance,
            entry + 3 * risk_distance,
        ]
    else:
        targets = [
            entry - risk_distance,
            entry - 2 * risk_distance,
            entry - 3 * risk_distance,
        ]

    if score >= 90:
        score_label = "Very Strong"
    elif score >= 70:
        score_label = "Strong"
    else:
        score_label = "Medium"

    result.update({
        "status": direction,
        "strategy": strategy,
        "score": score,
        "score_label": score_label,
        "reason": "; ".join(reasons),
        "entry": entry,
        "stop_loss": stop_loss,
        "take_profits": targets,
        "risk_distance": risk_distance,
        "risk_reward": [1, 2, 3],
    })

    return result


def format_signal(symbol, result):
    """Format a readable Telegram analysis report."""

    status = result.get("status", "WAIT")
    timeframe = result.get("timeframe", "15m")

    if symbol in ("BTC/USD", "XAU/USD"):
        digits = 2
    elif "JPY" in symbol:
        digits = 3
    else:
        digits = 5

    def price(value):
        return f"{float(value):.{digits}f}"

    if status == "WAIT":
        return (
            f"FOREX HUNTER AI\n"
            f"Market: {symbol}\n"
            f"Timeframe: {timeframe}\n"
            f"Status: WAIT\n\n"
            f"Reason: {result.get('reason', 'No setup.')}\n\n"
            "Analysis only. No orders have been placed."
        )

    targets = result["take_profits"]

    return (
        f"FOREX HUNTER AI — {status}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"Market: {symbol}\n"
        f"Direction: {status}\n"
        f"Timeframe: {timeframe}\n"
        f"Strategy: {result.get('strategy')}\n"
        f"Setup score: {result.get('score')}/100 "
        f"({result.get('score_label')})\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"Entry: {price(result['entry'])}\n"
        f"Stop Loss: {price(result['stop_loss'])}\n"
        f"TP1 (1R): {price(targets[0])}\n"
        f"TP2 (2R): {price(targets[1])}\n"
        f"TP3 (3R): {price(targets[2])}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"ADX: {result.get('adx', 0):.1f}\n"
        f"RSI: {result.get('rsi', 0):.1f}\n"
        f"1H Context: {result.get('higher_timeframe')}\n"
        f"Explanation: {result.get('reason')}\n\n"
        "Manual analysis only. No broker orders are placed."
    )
