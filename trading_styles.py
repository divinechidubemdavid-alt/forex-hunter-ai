"""Trading-style policy shared by manual analysis and automatic delivery.

Existing strategy logic is reused by all three styles. This module only
defines each style's allowed entry timeframes and required higher-timeframe
confirmation. Automatic Scanner and Strong Signals are deliberately Day-only.
"""

STYLE_CONFIGS = {
    "SCALPING": {
        "label": "SCALPING TRADE",
        "timeframes": ("1m", "5m"),
        "confirmations": {
            "1m": ("5m", "15m"),
            "5m": ("15m", "30m"),
        },
        "has_trade_levels": False,
        "score_weights": {"adx": 10, "ema": 10, "rsi": 20, "candle": 20, "setup": 15, "volatility": 10, "directional": 15},
        "minimum_score": 72,
        "minimum_adx": 15.0,
    },
    "DAY": {
        "label": "DAY TRADE",
        "timeframes": ("15m", "30m"),
        "confirmations": {
            "15m": ("1h", "4h"),
            "30m": ("1h", "4h"),
        },
        "has_trade_levels": True,
        "score_weights": {"adx": 20, "ema": 15, "rsi": 10, "candle": 10, "setup": 20, "volatility": 10, "directional": 15},
        "minimum_score": 70,
        "minimum_adx": 20.0,
    },
    "SWING": {
        "label": "SWING TRADE",
        "timeframes": ("1h", "4h", "1day"),
        "confirmations": {
            "1h": ("4h", "1day"),
            "4h": ("1day",),
            "1day": ("1week",),
        },
        "has_trade_levels": True,
        "score_weights": {"adx": 25, "ema": 20, "rsi": 10, "candle": 10, "setup": 15, "volatility": 5, "directional": 15},
        "minimum_score": 75,
        "minimum_adx": 22.0,
    },
}

AUTOMATIC_TRADING_STYLE = "DAY"
STRONG_SIGNALS_TRADING_STYLE = "DAY"


def normalise_trading_style(style):
    value = str(style or "DAY").strip().upper().replace(" ", "_")
    aliases = {
        "SCALP": "SCALPING",
        "SCALPING_TRADE": "SCALPING",
        "DAY_TRADE": "DAY",
        "DAY_TRADING": "DAY",
        "DAILY": "DAY",
        "SWING_TRADE": "SWING",
        "SWING_TRADING": "SWING",
    }
    value = aliases.get(value, value)
    if value not in STYLE_CONFIGS:
        raise ValueError(f"Unsupported trading style: {style}")
    return value


def style_label(style):
    return STYLE_CONFIGS[normalise_trading_style(style)]["label"]


def style_timeframes(style):
    return STYLE_CONFIGS[normalise_trading_style(style)]["timeframes"]


def required_higher_timeframes(style, timeframe):
    config = STYLE_CONFIGS[normalise_trading_style(style)]
    value = str(timeframe).lower()
    if value not in config["timeframes"]:
        raise ValueError(
            f"{value} is not an allowed timeframe for {style_label(style)}"
        )
    return config["confirmations"][value]


def style_has_trade_levels(style):
    return bool(STYLE_CONFIGS[normalise_trading_style(style)]["has_trade_levels"])


def is_automatic_style(style):
    return normalise_trading_style(style) == AUTOMATIC_TRADING_STYLE


def is_strong_signal_style(style):
    return normalise_trading_style(style) == STRONG_SIGNALS_TRADING_STYLE


def style_score_profile(style):
    return dict(STYLE_CONFIGS[normalise_trading_style(style)]["score_weights"])


def style_minimum_score(style):
    return int(STYLE_CONFIGS[normalise_trading_style(style)]["minimum_score"])


def style_minimum_adx(style):
    return float(STYLE_CONFIGS[normalise_trading_style(style)]["minimum_adx"])
