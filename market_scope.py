"""Shared weekday/weekend market eligibility rules.

All market-facing features use these helpers so the manual scanner,
Strong Signals and automatic scanner cannot drift into different lists.
"""

from config import WEEKDAY_MARKETS, WEEKEND_MARKETS


def get_session_name(weekday):
    """Return weekday/weekend for datetime.weekday() (Monday=0, Sunday=6)."""
    return "weekend" if int(weekday) >= 5 else "weekday"


def get_session_markets(markets, weekday):
    """Return configured markets allowed in the current session."""
    configured = set(markets)
    allowed = WEEKEND_MARKETS if int(weekday) >= 5 else WEEKDAY_MARKETS
    return [symbol for symbol in allowed if symbol in configured]


def is_market_allowed(symbol, weekday):
    """Check whether a symbol belongs to the current configured session."""
    allowed = WEEKEND_MARKETS if int(weekday) >= 5 else WEEKDAY_MARKETS
    return symbol in allowed


def get_strong_signal_markets(markets, weekday):
    """Compatibility wrapper used by Strong Signals."""
    return get_session_markets(markets, weekday)
