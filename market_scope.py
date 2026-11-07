"""Market selection helpers for Strong Signals."""


def get_strong_signal_markets(markets, weekday):
    """Return BTC/USD only on weekends, otherwise all configured markets.

    weekday follows datetime.weekday(): Monday=0 through Sunday=6.
    """
    configured = list(markets)
    if weekday >= 5:
        return ["BTC/USD"] if "BTC/USD" in configured else []
    return configured
