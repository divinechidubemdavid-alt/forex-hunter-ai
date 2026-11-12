

from io import BytesIO

import matplotlib
matplotlib.use("Agg")

import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.patches as patches
import numpy as np
import pandas as pd

from config import ADX_MINIMUM
from strategy import calculate_indicators


BACKGROUND = "#080B0A"
PANEL = "#101613"
GREEN = "#39FF88"
GOLD = "#D6AD45"
RED = "#FF5263"
TEXT = "#E8F0EA"
MUTED = "#829188"




def _confirmed_swings(data, left=2, right=2):
    """Return confirmed local swing highs/lows (no unclosed future bars)."""
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


def _draw_market_annotations(ax, data, x_values):
    """Draw conservative, confirmed structure and price-action annotations."""
    if len(data) < 12:
        return

    highs, lows = _confirmed_swings(data)
    last_close = float(data["close"].iloc[-1])
    last_high = float(data["high"].iloc[-1])
    last_low = float(data["low"].iloc[-1])
    last_atr = float((data["high"] - data["low"]).rolling(14).mean().iloc[-1])
    if not np.isfinite(last_atr) or last_atr <= 0:
        last_atr = max(abs(last_close) * 0.001, 1e-8)

    # Label the latest confirmed swing points as HH/LH and HL/LL.
    for points, column, is_high in ((highs, "high", True), (lows, "low", False)):
        recent = points[-4:]
        previous_value = None
        for idx in recent:
            value = float(data[column].iloc[idx])
            if previous_value is None:
                label = "SH" if is_high else "SL"
            else:
                delta = value - previous_value
                if abs(delta) <= last_atr * 0.08:
                    label = "EQH" if is_high else "EQL"
                elif (delta > 0) == is_high:
                    label = "HH" if is_high else "HL"
                else:
                    label = "LH" if is_high else "LL"
            color = RED if is_high else GREEN
            offset = last_atr * (0.18 if is_high else -0.18)
            ax.annotate(
                label,
                (x_values[idx], value + offset),
                color=color,
                fontsize=8,
                fontweight="bold",
                ha="center",
                va="bottom" if is_high else "top",
                zorder=7,
                bbox={"boxstyle": "round,pad=0.18", "fc": BACKGROUND, "ec": color, "alpha": 0.85, "lw": 0.6},
            )
            previous_value = value

    # Infer structure only when both swing sequences support it.
    structure = "RANGE / DEVELOPING"
    if len(highs) >= 2 and len(lows) >= 2:
        higher_high = data["high"].iloc[highs[-1]] > data["high"].iloc[highs[-2]]
        higher_low = data["low"].iloc[lows[-1]] > data["low"].iloc[lows[-2]]
        lower_high = data["high"].iloc[highs[-1]] < data["high"].iloc[highs[-2]]
        lower_low = data["low"].iloc[lows[-1]] < data["low"].iloc[lows[-2]]
        if higher_high and higher_low:
            structure = "BULLISH STRUCTURE  HH + HL"
        elif lower_high and lower_low:
            structure = "BEARISH STRUCTURE  LH + LL"

    structure_color = GREEN if structure.startswith("BULLISH") else RED if structure.startswith("BEARISH") else GOLD
    ax.text(
        0.015, 0.975, structure,
        transform=ax.transAxes, ha="left", va="top",
        color=structure_color, fontsize=9, fontweight="bold",
        bbox={"boxstyle": "round,pad=0.35", "fc": BACKGROUND, "ec": structure_color, "alpha": 0.92},
        zorder=10,
    )

    # Supply and demand zones are drawn from the wick/body of confirmed pivots.
    # Keep only the two most recent zones of each type to avoid clutter.
    zone_end = x_values[-1] + (x_values[-1] - x_values[-2] if len(x_values) > 1 else 0.0005) * 3
    for idx in highs[-2:]:
        candle = data.iloc[idx]
        lower = max(float(candle["open"]), float(candle["close"]))
        upper = float(candle["high"])
        if upper > lower:
            rect = patches.Rectangle(
                (x_values[idx], lower), zone_end - x_values[idx], upper - lower,
                facecolor=RED, edgecolor=RED, alpha=0.13, linewidth=0.8,
                linestyle="--", zorder=1,
            )
            ax.add_patch(rect)
            ax.text(x_values[idx], upper, "SUPPLY", color=RED, fontsize=7, va="bottom", zorder=6)

    for idx in lows[-2:]:
        candle = data.iloc[idx]
        lower = float(candle["low"])
        upper = min(float(candle["open"]), float(candle["close"]))
        if upper > lower:
            rect = patches.Rectangle(
                (x_values[idx], lower), zone_end - x_values[idx], upper - lower,
                facecolor=GREEN, edgecolor=GREEN, alpha=0.13, linewidth=0.8,
                linestyle="--", zorder=1,
            )
            ax.add_patch(rect)
            ax.text(x_values[idx], lower, "DEMAND", color=GREEN, fontsize=7, va="top", zorder=6)

    # Mark a break of the latest confirmed swing only after a candle closes beyond it.
    if highs:
        resistance_idx = highs[-1]
        resistance = float(data["high"].iloc[resistance_idx])
        if last_close > resistance + last_atr * 0.05:
            ax.plot(
                [x_values[resistance_idx], zone_end], [resistance, resistance],
                color=GREEN, linestyle=":", linewidth=1.2, zorder=5,
            )
            ax.annotate(
                "BOS ↑  STRUCTURE BREAK",
                (x_values[-1], resistance),
                xytext=(-8, 10), textcoords="offset points",
                color=GREEN, fontsize=7, ha="right", zorder=8,
            )

    if lows:
        support_idx = lows[-1]
        support = float(data["low"].iloc[support_idx])
        if last_close < support - last_atr * 0.05:
            ax.plot(
                [x_values[support_idx], zone_end], [support, support],
                color=RED, linestyle=":", linewidth=1.2, zorder=5,
            )
            ax.annotate(
                "BOS ↓  STRUCTURE BREAK",
                (x_values[-1], support),
                xytext=(-8, -12), textcoords="offset points",
                color=RED, fontsize=7, ha="right", zorder=8,
            )

    # Draw the most recent meaningful two-pivot trendline and label a confirmed
    # break/retest only when the latest completed candle supports that reading.
    trend_points = lows[-2:] if len(lows) >= 2 else highs[-2:] if len(highs) >= 2 else []
    if len(trend_points) == 2:
        first, second = trend_points
        first_is_low = len(lows) >= 2
        p1 = float(data["low" if first_is_low else "high"].iloc[first])
        p2 = float(data["low" if first_is_low else "high"].iloc[second])
        slope = (p2 - p1) / max(second - first, 1)
        projected = p2 + slope * (len(data) - 1 - second)
        line_color = GREEN if first_is_low and slope > 0 else RED if not first_is_low and slope < 0 else GOLD
        ax.plot(
            [x_values[first], zone_end],
            [p1, p2 + slope * (len(data) - 1 + 3 - second)],
            color=line_color, linestyle="-.", linewidth=1.1, alpha=0.9, zorder=4,
        )
        line_at_last = projected
        previous_line = p2 + slope * (len(data) - 2 - second)
        previous_close = float(data["close"].iloc[-2])
        break_down = previous_close >= previous_line and last_close < line_at_last - last_atr * 0.05
        break_up = previous_close <= previous_line and last_close > line_at_last + last_atr * 0.05
        touches_line = last_low <= line_at_last + last_atr * 0.2 and last_high >= line_at_last - last_atr * 0.2
        if break_down or break_up:
            label = "TRENDLINE BREAK ↓" if break_down else "TRENDLINE BREAK ↑"
            ax.annotate(label, (x_values[-1], last_close), xytext=(-8, -22 if break_down else 12),
                        textcoords="offset points", color=RED if break_down else GREEN,
                        fontsize=7, ha="right", fontweight="bold", zorder=9)
        elif touches_line and abs(last_close - line_at_last) <= last_atr * 0.25:
            ax.annotate("TRENDLINE RETEST", (x_values[-1], line_at_last),
                        xytext=(-8, 12), textcoords="offset points",
                        color=GOLD, fontsize=7, ha="right", fontweight="bold", zorder=9)


def create_market_chart(
    candles,
    symbol,
    timeframe,
    entry=None,
    stop_loss=None,
    take_profits=None,
    strategy=None,
    signal_stage=None,
):
    """
    Generate a PNG candlestick chart with market-structure overlays.

    Returns a BytesIO object suitable for Telegram photo messages.
    """

    if candles is None or candles.empty:
        raise ValueError("No candles are available to draw.")

    required = ["open", "high", "low", "close"]

    if any(column not in candles.columns for column in required):
        raise ValueError("Candle data is missing OHLC values.")

    # Use the strategy engine as the single source of truth for every plotted indicator.
    data = calculate_indicators(candles).tail(80).copy()

    # EMA 50 is a visual higher-trend reference only; it does not alter signal rules.
    data["ema50"] = data["close"].ewm(span=50, adjust=False).mean()

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

    fig, axes = plt.subplots(
        4, 1, figsize=(13, 10), sharex=True,
        gridspec_kw={"height_ratios": [4.2, 1.1, 1.1, 1.0]},
    )
    ax, rsi_ax, adx_ax, atr_ax = axes

    fig.patch.set_facecolor(BACKGROUND)
    for panel_ax in axes:
        panel_ax.set_facecolor(PANEL)

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

    # Overlay the exact EMA values calculated by strategy.calculate_indicators().
    ax.plot(x_values, data["ema9"], color=GREEN, linewidth=1.35, label="EMA 9", zorder=4)
    ax.plot(x_values, data["ema21"], color=GOLD, linewidth=1.35, label="EMA 21", zorder=4)
    ax.plot(x_values, data["ema50"], color="#8DB8FF", linewidth=1.15, label="EMA 50", zorder=3)

    latest = data.iloc[-1]
    for column, label, color in (("support", "SUPPORT", GREEN), ("resistance", "RESISTANCE", RED)):
        level = float(latest.get(column, np.nan))
        if np.isfinite(level):
            ax.axhline(level, color=color, linestyle=":", linewidth=0.9, alpha=0.8, label=f"{label}: {level:g}")

    # RSI panel: momentum context, not a standalone buy/sell trigger.
    rsi_ax.plot(x_values, data["rsi"], color=GOLD, linewidth=1.15, label="RSI 14")
    for level in (30, 50, 70):
        rsi_ax.axhline(level, color=MUTED, linestyle=":" if level != 50 else "--", linewidth=0.7, alpha=0.65)
    rsi_ax.set_ylim(0, 100)
    rsi_ax.set_ylabel("RSI", color=TEXT, fontsize=8)
    rsi_value = float(latest["rsi"])
    rsi_ax.set_title(f"RSI 14  |  {rsi_value:.1f}", color=GOLD, fontsize=9, loc="left", pad=3)

    # ADX and directional indicators: strength plus direction.
    adx_ax.plot(x_values, data["adx"], color=GOLD, linewidth=1.1, label="ADX 14")
    adx_ax.plot(x_values, data["plus_di"], color=GREEN, linewidth=0.9, label="+DI")
    adx_ax.plot(x_values, data["minus_di"], color=RED, linewidth=0.9, label="-DI")
    adx_ax.axhline(ADX_MINIMUM, color=MUTED, linestyle=":", linewidth=0.8, alpha=0.8)
    adx_ax.set_ylabel("ADX / DI", color=TEXT, fontsize=8)
    adx_ax.set_title(
        f"ADX {float(latest['adx']):.1f}  |  +DI {float(latest['plus_di']):.1f}  |  -DI {float(latest['minus_di']):.1f}  |  strength guide {ADX_MINIMUM:g}",
        color=GOLD, fontsize=9, loc="left", pad=3,
    )
    adx_ax.legend(loc="upper right", fontsize=7, ncol=3, facecolor=BACKGROUND, edgecolor="#344238", labelcolor=TEXT)

    # ATR panel: volatility measurement, not direction.
    atr_ax.plot(x_values, data["atr"], color="#8DB8FF", linewidth=1.05, label="ATR 14")
    atr_ax.set_ylabel("ATR", color=TEXT, fontsize=8)
    atr_ax.set_title(f"ATR 14  |  {float(latest['atr']):g}", color="#8DB8FF", fontsize=9, loc="left", pad=3)

    _draw_market_annotations(ax, data, x_values)
    if strategy:
        tag = str(strategy).upper()
        if signal_stage:
            tag += f"  |  {str(signal_stage).replace('_', ' ')}"
        ax.text(
            0.985, 0.975, tag, transform=ax.transAxes,
            ha="right", va="top", color=GOLD, fontsize=8, fontweight="bold",
            bbox={"boxstyle": "round,pad=0.3", "fc": BACKGROUND, "ec": GOLD, "alpha": 0.92},
            zorder=11,
        )

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

    atr_ax.set_xlabel("CANDLE TIME (UTC)", color=MUTED)
    ax.set_ylabel("PRICE", color=MUTED)

    atr_ax.xaxis_date()
    atr_ax.xaxis.set_major_formatter(
        mdates.DateFormatter("%m-%d %H:%M")
    )

    tick_count = min(7, len(data))
    tick_positions = np.linspace(
        0,
        len(data) - 1,
        tick_count,
        dtype=int,
    )

    atr_ax.set_xticks(x_values[tick_positions])
    atr_ax.tick_params(axis="x", colors=TEXT, labelrotation=25, labelsize=8)
    for panel_ax in axes:
        panel_ax.tick_params(axis="y", colors=TEXT, labelsize=8)
        for spine in panel_ax.spines.values():
            spine.set_color("#27332C")
        panel_ax.grid(True, color="#26332B", linestyle=":", linewidth=0.6, alpha=0.65)

    legend = ax.legend(
        loc="upper left",
        fontsize=7,
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
