from io import BytesIO

import matplotlib
matplotlib.use("Agg")

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import matplotlib.patches as patches
import numpy as np
import pandas as pd

from trading_styles import style_label


BACKGROUND = "#080B0A"
PANEL = "#101613"
GREEN = "#39FF88"
GOLD = "#D6AD45"
RED = "#FF5263"
TEXT = "#E8F0EA"
MUTED = "#829188"


def _confirmed_swings(data, left=2, right=2):
    """Return confirmed local swing highs/lows using completed candles."""
    highs = []
    lows = []
    high_values = data["high"].to_numpy(dtype=float)
    low_values = data["low"].to_numpy(dtype=float)

    for i in range(left, len(data) - right):
        high_window = high_values[i-left:i+right+1]
        low_window = low_values[i-left:i+right+1]
        if high_values[i] == np.max(high_window) and np.count_nonzero(high_window == high_values[i]) == 1:
            highs.append(i)
        if low_values[i] == np.min(low_window) and np.count_nonzero(low_window == low_values[i]) == 1:
            lows.append(i)

    return highs, lows


def _supply_demand_zone_bounds(data):
    """Build price zones from the latest confirmed swing supply/demand candles."""
    highs, lows = _confirmed_swings(data)
    zones = []

    for idx in highs[-2:]:
        candle = data.iloc[idx]
        lower = max(float(candle["open"]), float(candle["close"]))
        upper = float(candle["high"])
        if np.isfinite(lower) and np.isfinite(upper) and upper > lower:
            zones.append({"type": "SUPPLY", "index": idx, "lower": lower, "upper": upper})

    for idx in lows[-2:]:
        candle = data.iloc[idx]
        lower = float(candle["low"])
        upper = min(float(candle["open"]), float(candle["close"]))
        if np.isfinite(lower) and np.isfinite(upper) and upper > lower:
            zones.append({"type": "DEMAND", "index": idx, "lower": lower, "upper": upper})

    return zones


def _draw_supply_demand_zones(ax, data, x_values):
    """Draw only confirmed supply and demand zones on the price chart."""
    zones = _supply_demand_zone_bounds(data)
    if len(x_values) > 1:
        bar_width = float(np.median(np.diff(x_values)))
    else:
        bar_width = 0.0005
    zone_end = x_values[-1] + bar_width * 3

    for zone in zones:
        is_supply = zone["type"] == "SUPPLY"
        color = RED if is_supply else GREEN
        start = x_values[zone["index"]]
        ax.add_patch(patches.Rectangle(
            (start, zone["lower"]),
            zone_end - start,
            zone["upper"] - zone["lower"],
            facecolor=color,
            edgecolor=color,
            alpha=0.16,
            linewidth=0.9,
            linestyle="--",
            zorder=1,
        ))
        ax.text(
            start, zone["upper"] if is_supply else zone["lower"],
            zone["type"], color=color, fontsize=7,
            va="bottom" if is_supply else "top", zorder=6,
            bbox={"boxstyle": "round,pad=0.15", "fc": BACKGROUND, "ec": color, "alpha": 0.85, "lw": 0.5},
        )

    return zones


def create_market_chart(
    candles,
    symbol,
    timeframe,
    entry=None,
    stop_loss=None,
    take_profits=None,
    strategy=None,
    signal_stage=None,
    trading_style=None,
):
    """
    Generate a clean PNG candlestick chart with supply and demand zones only.

    Strategy indicators remain in the analysis engine; they are intentionally
    not plotted here. Returns a BytesIO object for Telegram photo messages.
    """
    if candles is None or candles.empty:
        raise ValueError("No candles are available to draw.")

    required = ["open", "high", "low", "close"]
    if any(column not in candles.columns for column in required):
        raise ValueError("Candle data is missing OHLC values.")

    data = candles.copy()
    for column in required:
        data[column] = pd.to_numeric(data[column], errors="coerce")
    data = data.dropna(subset=required).tail(160).copy()

    if data.empty:
        raise ValueError("There are no valid candles to draw.")
    if not isinstance(data.index, pd.DatetimeIndex):
        raise ValueError("Candles must have a datetime index.")
    if data.index.tz is not None:
        data.index = data.index.tz_convert("UTC").tz_localize(None)

    fig, ax = plt.subplots(1, 1, figsize=(13, 7), sharex=True)
    fig.patch.set_facecolor(BACKGROUND)
    ax.set_facecolor(PANEL)

    x_values = mdates.date2num(data.index.to_pydatetime())
    if len(x_values) > 1:
        spacing = float(np.median(np.diff(x_values)))
        candle_width = max(spacing * 0.62, 0.0002)
    else:
        candle_width = 0.0005

    for x, (_, candle) in zip(x_values, data.iterrows()):
        open_price = float(candle["open"])
        high_price = float(candle["high"])
        low_price = float(candle["low"])
        close_price = float(candle["close"])
        candle_color = GREEN if close_price >= open_price else RED

        ax.vlines(x, low_price, high_price, color=candle_color, linewidth=1.1, zorder=2)
        bottom = min(open_price, close_price)
        height = abs(close_price - open_price)
        if height == 0:
            height = max(abs(high_price - low_price) * 0.015, abs(close_price) * 0.000001, 1e-10)

        ax.add_patch(patches.Rectangle(
            (x - candle_width / 2, bottom),
            candle_width,
            height,
            facecolor=candle_color,
            edgecolor=candle_color,
            linewidth=0.8,
            zorder=3,
        ))

    _draw_supply_demand_zones(ax, data, x_values)

    # Trade-plan levels are retained as trade annotations, not indicators.
    levels = [("ENTRY", entry, GOLD), ("STOP LOSS", stop_loss, RED)]
    for index, target in enumerate(take_profits or [], start=1):
        levels.append((f"TP{index}", target, GREEN))

    for label, value, color in levels:
        if value is None:
            continue
        try:
            price = float(value)
        except (TypeError, ValueError):
            continue
        if np.isfinite(price):
            ax.axhline(price, color=color, linestyle="--", linewidth=1.05, alpha=0.9, label=f"{label}: {price:g}")

    title = f"{symbol}  |  {timeframe.upper()}"
    if trading_style:
        try:
            label = style_label(trading_style)
        except ValueError:
            label = str(trading_style)
        title += f"  |  {label}"
    if strategy:
        title += f"  |  {str(strategy).upper()}"
    if signal_stage:
        title += f"  |  {str(signal_stage).replace('_', ' ').upper()}"

    ax.set_title(title, color=GOLD, fontsize=14, fontweight="bold", pad=14)
    ax.set_ylabel("PRICE", color=MUTED)
    ax.set_xlabel("CANDLE TIME (UTC)", color=MUTED)
    ax.xaxis_date()
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M"))
    tick_count = min(8, len(data))
    tick_positions = np.linspace(0, len(data) - 1, tick_count, dtype=int)
    ax.set_xticks(x_values[tick_positions])
    ax.tick_params(axis="x", colors=TEXT, labelrotation=25, labelsize=8)
    ax.tick_params(axis="y", colors=TEXT, labelsize=8)
    for spine in ax.spines.values():
        spine.set_color("#27332C")
    ax.grid(True, color="#26332B", linestyle=":", linewidth=0.6, alpha=0.55)

    legend = ax.legend(loc="upper left", fontsize=7, facecolor=BACKGROUND, edgecolor="#344238")
    if legend:
        for label in legend.get_texts():
            label.set_color(TEXT)

    fig.tight_layout()
    output = BytesIO()
    fig.savefig(output, format="png", dpi=150, facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)
    output.seek(0)
    output.name = f"{str(symbol).replace('/', '_')}_{timeframe}.png"
    return output
