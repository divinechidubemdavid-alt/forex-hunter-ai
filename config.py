

import os

BOT_NAME = "Forex Hunter AI"

# Secrets are read from Codespaces environment variables.
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TWELVE_DATA_API_KEY = os.getenv("TWELVE_DATA_API_KEY", "").strip()
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID", "").strip()

# Eight approved markets.
MARKETS = {
    "XAU/USD": "XAU/USD",
    "EUR/USD": "EUR/USD",
    "GBP/USD": "GBP/USD",
    "USD/JPY": "USD/JPY",
    "AUD/USD": "AUD/USD",
    "USD/CAD": "USD/CAD",
    "USD/CHF": "USD/CHF",
    "BTC/USD": "BTC/USD",
}

# Seven manual analysis timeframes.
TIMEFRAMES = (
    "1m", "5m", "15m", "30m", "1h", "4h", "1day"
)

DEFAULT_TIMEFRAME = os.getenv(
    "DEFAULT_TIMEFRAME", "15m"
).lower()

if DEFAULT_TIMEFRAME not in TIMEFRAMES:
    DEFAULT_TIMEFRAME = "15m"

# Automatic scanner settings.
AUTO_SCAN_TIMEFRAME = "15m"
HIGHER_TIMEFRAME = "1h"
AUTO_SCAN_MIN_SCORE = 70

# Signal quality bands.
SCORE_WEAK_MAX = 49
SCORE_MEDIUM_MAX = 69
SCORE_STRONG_MAX = 89
SCORE_VERY_STRONG_MAX = 100

# Indicator settings.
EMA_FAST = 9
EMA_SLOW = 21
ADX_PERIOD = 14
ADX_MINIMUM = 20.0
RSI_PERIOD = 14
ATR_PERIOD = 14

# Risk/reward targets.
RISK_REWARD_TARGETS = (1.0, 2.0, 3.0)
PARTIAL_EXIT_FRACTIONS = (1 / 3, 1 / 3, 1 / 3)

# Twelve Data safety limit.
# This is a local ceiling, not a claim about provider credit pricing.
TWELVE_DATA_DAILY_CREDIT_LIMIT = 800

# Reserve 100 local request slots for manual analysis and monitoring.
API_RESERVE_REQUESTS = 100

# Market-data settings.
CACHE_TTL_SECONDS = 60
MAX_CANDLE_AGE_MINUTES = 20
CANDLE_OUTPUT_SIZE = 500
MIN_REQUEST_INTERVAL_SECONDS = 8.0

# Signal monitoring.
MONITOR_INTERVAL_MINUTES = 15
ACTIVE_SIGNAL_MONITOR_TIMEFRAME = "15min"

# Nigeria timezone is the default display timezone.
DEFAULT_TIMEZONE = "Africa/Lagos"

# Local persistent state.
STATE_FILE = "runtime_state.json"

# Application safety.
MANUAL_ONLY = True
PLACE_BROKER_ORDERS = False

# Adaptive scanner policy.
GOLD_BUDGET_SHARE = 0.50

# Single source of truth for session eligibility.
# Weekday session: supported forex pairs plus Gold.
WEEKDAY_MARKETS = (
    "XAU/USD",
    "EUR/USD",
    "GBP/USD",
    "USD/JPY",
    "AUD/USD",
    "USD/CAD",
    "USD/CHF",
    "BTC/USD",
)

# Weekend session: Bitcoin only.
WEEKEND_MARKETS = ("BTC/USD",)

# Forex pairs are listed separately so the automatic scanner can
# respect the normal Friday close and Sunday reopening window.
FOREX_MARKETS = (
    "EUR/USD",
    "GBP/USD",
    "USD/JPY",
    "AUD/USD",
    "USD/CAD",
    "USD/CHF",
)

# Telegram menu labels.
MAIN_MENU = (
    "🔎 Market Scanner",
    "⭐ Strong Signals",
    "📊 My Tracked Signals",
    "📈 Performance",
    "📚 Signal History",
    "⚙️ Settings",
    "❓ Help",
)

# Scanner must not consume the reserved API allowance.
AUTO_SCANNER_ENABLED = True

if TWELVE_DATA_DAILY_CREDIT_LIMIT < 1:
    raise ValueError("Daily API limit must be at least 1.")

if not 0 <= GOLD_BUDGET_SHARE <= 1:
    raise ValueError("Gold budget share must be between 0 and 1.")
