

from io import BytesIO

import matplotlib
matplotlib.use("Agg")

import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.patches as patches
import numpy as np
import pandas as pd


BACKGROUND = "#080B0A"
PANEL = "#101613"
GREEN = "#39FF88"
GOLD = "#D6AD45"
RED = "#FF5263"
TEXT = "#E8F0EA"
MUTED = "#829188"


def create_market_chart(
    candles,
    symbol,
    timeframe,
    entry=None,
    stop_loss=None,
    take_profits=None,
):
    """
    Generate a PNG candlestick chart.

    Returns a BytesIO object suitable for Telegram photo messages.
    """

    if candles is None or candles.empty:
        raise ValueError("No candles are available to draw.")

    required = ["open", "high", "low", "close"]

    if any(column not in candles.columns for column in required):
        raise ValueError("Candle data is missing OHLC values.")

    data = candles.tail(80).copy()

    for column in required:
        data[column] = pd.to_numeric(
            data[column],
            errors="coerce",
        )

    data = data.dropna(subset=required)

    if data.empty:
        raise ValueError("There are no valid candles to draw.")

    if not isinstance(data.index, pd.DatetimeIndex):
        raise ValueError("Candles must have a datetime index.")

    if data.index.tz is not None:
        data.index = data.index.tz_convert("UTC").tz_localize(None)

    fig, ax = plt.subplots(figsize=(13, 7))

    fig.patch.set_facecolor(BACKGROUND)
    ax.set_facecolor(PANEL)

    x_values = mdates.date2num(data.index.to_pydatetime())

    if len(x_values) > 1:
        spacing = np.median(np.diff(x_values))
        candle_width = max(spacing * 0.62, 0.0002)
    else:
        candle_width = 0.0005

    for x, (_, candle) in zip(x_values, data.iterrows()):
        open_price = float(candle["open"])
        high_price = float(candle["high"])
        low_price = float(candle["low"])
        close_price = float(candle["close"])

        rising = close_price >= open_price
        candle_color = GREEN if rising else RED

        ax.vlines(
            x,
            low_price,
            high_price,
            color=candle_color,
            linewidth=1.1,
            zorder=2,
        )

        bottom = min(open_price, close_price)
        height = abs(close_price - open_price)

        if height == 0:
            height = max(
                abs(high_price - low_price) * 0.015,
                abs(close_price) * 0.000001,
                1e-10,
            )

        body = patches.Rectangle(
            (x - candle_width / 2, bottom),
            candle_width,
            height,
            facecolor=candle_color,
            edgecolor=candle_color,
            linewidth=0.8,
            zorder=3,
        )

        ax.add_patch(body)

    levels = [
        ("ENTRY", entry, GOLD),
        ("STOP LOSS", stop_loss, RED),
    ]

    for index, target in enumerate(take_profits or [], start=1):
        levels.append((f"TP{index}", target, GREEN))

    for label, value, color in levels:
        if value is None:
            continue

        try:
            price = float(value)
        except (TypeError, ValueError):
            continue

        if not np.isfinite(price):
            continue

        ax.axhline(
            price,
            color=color,
            linestyle="--",
            linewidth=1.15,
            alpha=0.9,
            label=f"{label}: {price:g}",
        )

    ax.set_title(
        f"{symbol}  |  {timeframe.upper()}",
        color=GOLD,
        fontsize=16,
        fontweight="bold",
        pad=16,
    )

    ax.set_xlabel("CANDLE TIME (UTC)", color=MUTED)
    ax.set_ylabel("PRICE", color=MUTED)

    ax.xaxis_date()
    ax.xaxis.set_major_formatter(
        mdates.DateFormatter("%m-%d %H:%M")
    )

    tick_count = min(7, len(data))
    tick_positions = np.linspace(
        0,
        len(data) - 1,
        tick_count,
        dtype=int,
    )

    ax.set_xticks(x_values[tick_positions])
    ax.tick_params(
        axis="x",
        colors=TEXT,
        labelrotation=25,
        labelsize=8,
    )
    ax.tick_params(axis="y", colors=TEXT, labelsize=9)

    for spine in ax.spines.values():
        spine.set_color("#27332C")

    ax.grid(
        True,
        color="#26332B",
        linestyle=":",
        linewidth=0.7,
        alpha=0.7,
    )

    legend = ax.legend(
        loc="best",
        fontsize=8,
        facecolor=BACKGROUND,
        edgecolor="#344238",
    )

    if legend:
        for label in legend.get_texts():
            label.set_color(TEXT)

    fig.tight_layout()

    output = BytesIO()

    fig.savefig(
        output,
        format="png",
        dpi=150,
        facecolor=fig.get_facecolor(),
        bbox_inches="tight",
    )

    plt.close(fig)

    output.seek(0)
    output.name = (
        f"{str(symbol).replace('/', '_')}_{timeframe}.png"
    )

    return output
