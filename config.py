

import os

BOT_NAME = "Forex Hunter AI"

# Read secrets from GitHub Codespaces Secrets / environment variables.
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TWELVE_DATA_API_KEY = os.getenv("TWELVE_DATA_API_KEY", "").strip()
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID", "").strip()

# Markets the user can select for an individual scan.
MARKETS = {
    "EUR/USD": "EUR/USD",
    "GBP/USD": "GBP/USD",
    "USD/JPY": "USD/JPY",
    "XAU/USD": "XAU/USD",
}

# Supported analysis timeframes.
TIMEFRAMES = ("1m", "5m", "15m")
DEFAULT_TIMEFRAME = os.getenv("DEFAULT_TIMEFRAME", "5m").lower()

# Trade-monitoring settings.
MONITOR_INTERVAL_MINUTES = int(
    os.getenv("MONITOR_INTERVAL_MINUTES", "15")
)

# Local daily safety budget for Twelve Data requests.
# Keep this at or below the daily allowance of your API plan.
TWELVE_DATA_DAILY_CREDIT_LIMIT = int(
    os.getenv("TWELVE_DATA_DAILY_CREDIT_LIMIT", "800")
)

# Minimum time between API requests.
MIN_REQUEST_INTERVAL_SECONDS = float(
    os.getenv("MIN_REQUEST_INTERVAL_SECONDS", "7.6")
)

# Market-data safeguards.
CACHE_TTL_SECONDS = 30
MAX_CANDLE_AGE_MINUTES = 3

# EMA 9/21 + ADX strategy.
ADX_MINIMUM = 20.0

# Local runtime state file.
STATE_FILE = "runtime_state.json"

# Validate configuration.
if DEFAULT_TIMEFRAME not in TIMEFRAMES:
    DEFAULT_TIMEFRAME = "5m"

if MONITOR_INTERVAL_MINUTES < 5:
    MONITOR_INTERVAL_MINUTES = 5

if TWELVE_DATA_DAILY_CREDIT_LIMIT < 1:
    TWELVE_DATA_DAILY_CREDIT_LIMIT = 1

if MIN_REQUEST_INTERVAL_SECONDS < 0:
    MIN_REQUEST_INTERVAL_SECONDS = 0.0
