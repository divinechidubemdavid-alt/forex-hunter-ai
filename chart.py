from io import BytesIO

import matplotlib
matplotlib.use("Agg")

import matplotlib.pyplot as plt
import pandas as pd
import numpy as np


def create_market_chart(
    candles,
    symbol,
    timeframe,
    entry=None,
    stop_loss=None,
    take_profits=None,
):
    """Create a PNG chart from OHLC candles."""
    data = candles.tail(60).copy()

    if data.empty:
        raise ValueError("No candles are available to draw.")

    fig, ax = plt.subplots(figsize=(12, 6))

    x_values = np.arange(len(data))
    width = 0.6

    for position, (_, candle) in zip(x_values, data.iterrows()):
        open_price = float(candle["open"])
        high_price = float(candle["high"])
        low_price = float(candle["low"])
        close_price = float(candle["close"])

        rising = close_price >= open_price
        candle_color = "green" if rising else "red"

        ax.vlines(
            position, low_price, high_price,
            color=candle_color, linewidth=1,
        )

        bottom = min(open_price, close_price)
        height = abs(close_price - open_price)

        if height == 0:
            height = max(abs(high_price - low_price) * 0.002, 1e-8)

        ax.bar(
            position,
            height,
            bottom=bottom,
            width=width,
            color=candle_color,
            edgecolor=candle_color,
        )

    levels = [
        ("Entry", entry, "blue"),
        ("Stop Loss", stop_loss, "red"),
    ]

    for index, target in enumerate(take_profits or [], start=1):
        levels.append((f"TP{index}", target, "green"))

    for label, value, color in levels:
        if value is not None and np.isfinite(value):
            ax.axhline(
                float(value),
                linestyle="--",
                linewidth=1.2,
                color=color,
                label=f"{label}: {float(value):.5f}",
            )

    tick_positions = np.linspace(
        0, len(data) - 1, min(6, len(data)), dtype=int
    )
    tick_labels = [
        data.index[position].strftime("%m-%d %H:%M")
        for position in tick_positions
    ]

    ax.set_xticks(tick_positions)
    ax.set_xticklabels(tick_labels, rotation=25, ha="right")
    ax.set_title(f"{symbol} — {timeframe} | Forex Hunter AI")
    ax.set_xlabel("Candle time (UTC)")
    ax.set_ylabel("Price")
    ax.grid(True, alpha=0.25)
    ax.legend(loc="best", fontsize=8)

    fig.tight_layout()

    output = BytesIO()
    fig.savefig(output, format="png", dpi=140)
    plt.close(fig)
    output.seek(0)
    output.name = f"{symbol.replace('/', '_')}_{timeframe}.png"

    return output