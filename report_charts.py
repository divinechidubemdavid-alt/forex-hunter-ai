from io import BytesIO
from datetime import datetime
from zoneinfo import ZoneInfo

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from trading_styles import style_label

BG = "#080B0A"
PANEL = "#101613"
GREEN = "#39FF88"
GOLD = "#D6AD45"
RED = "#FF5263"
TEXT = "#E8F0EA"
MUTED = "#829188"
WAT = ZoneInfo("Africa/Lagos")


def _finish(fig, filename):
    output = BytesIO()
    fig.savefig(
        output, format="png", dpi=150,
        facecolor=fig.get_facecolor(), bbox_inches="tight",
    )
    plt.close(fig)
    output.seek(0)
    output.name = filename
    return output


def _base(title, subtitle):
    fig = plt.figure(figsize=(12, 7), facecolor=BG)
    fig.text(0.07, 0.93, "FOREX HUNTER AI", color=GOLD,
             fontsize=11, weight="bold")
    fig.text(0.07, 0.865, title, color=TEXT, fontsize=23, weight="bold")
    fig.text(0.07, 0.82, subtitle, color=MUTED, fontsize=10)
    return fig


def _style(signal):
    value = signal.get("trading_style")
    if not value:
        return "LEGACY / UNKNOWN"
    try:
        return style_label(value)
    except ValueError:
        return "LEGACY / UNKNOWN"


def _realised_r(signal):
    if signal.get("realised_r") is not None:
        try:
            return float(signal["realised_r"])
        except (TypeError, ValueError):
            pass
    flags = [bool(signal.get(f"tp{i}_hit")) for i in (1, 2, 3)]
    earned = sum((i + 1) / 3 for i, hit in enumerate(flags) if hit)
    if signal.get("status") == "STOP_LOSS":
        earned -= 1 - sum(1 / 3 for hit in flags if hit)
    return earned


def create_performance_report(signals, period_label="All time"):
    signals = list(signals or [])
    eligible = [
        s for s in signals
        if s.get("status") in {"TP3", "MANUAL_WIN", "STOP_LOSS"}
    ]
    wins = sum(s.get("status") in {"TP3", "MANUAL_WIN"} for s in eligible)
    losses = sum(s.get("status") == "STOP_LOSS" for s in eligible)
    manual_wins = sum(s.get("status") == "MANUAL_WIN" for s in eligible)
    win_rate = (wins / len(eligible) * 100) if eligible else 0.0
    total_r = sum(_realised_r(s) for s in signals
                  if s.get("status") not in {"MANUALLY_STOPPED", "INVALIDATED", "AMBIGUOUS"})
    tracked = sum(bool(s.get("created_at")) for s in signals)
    completed = sum(s.get("status") != "TRACKING" for s in signals)
    tp_counts = [sum(bool(s.get(f"tp{i}_hit")) for s in signals)
                 for i in (1, 2, 3)]
    style_counts = {
        label: sum(_style(s) == label for s in signals)
        for label in (
            "SCALPING TRADE", "DAY TRADE", "SWING TRADE", "LEGACY / UNKNOWN"
        )
    }

    fig = _base("PERFORMANCE CENTRE", f"{period_label}  •  {datetime.now(WAT):%d %b %Y, %H:%M} WAT")
    ax = fig.add_axes([0.07, 0.12, 0.86, 0.62])
    ax.set_facecolor(PANEL)
    ax.axis("off")

    cards = [
        ("TRACKED", str(tracked), GREEN),
        ("COMPLETED", str(completed), GOLD),
        ("WIN RATE", f"{win_rate:.1f}%", GREEN),
        ("CUMULATIVE R", f"{total_r:+.2f}R", GREEN if total_r >= 0 else RED),
    ]
    for i, (label, value, color) in enumerate(cards):
        x = 0.02 + i * 0.245
        ax.add_patch(plt.Rectangle((x, 0.68), 0.22, 0.27,
                                   transform=ax.transAxes, color="#17221B"))
        ax.text(x + 0.02, 0.86, label, transform=ax.transAxes,
                color=MUTED, fontsize=9, weight="bold")
        ax.text(x + 0.02, 0.74, value, transform=ax.transAxes,
                color=color, fontsize=20, weight="bold")

    ax.text(0.03, 0.53, "OUTCOMES", transform=ax.transAxes,
            color=GOLD, fontsize=11, weight="bold")
    ax.text(0.03, 0.40,
            f"Wins: {wins}     Losses: {losses}     Manual TP1/TP2 wins: {manual_wins}",
            transform=ax.transAxes, color=TEXT, fontsize=12)
    ax.text(0.03, 0.25,
            f"Target achievements     TP1: {tp_counts[0]}     TP2: {tp_counts[1]}     TP3: {tp_counts[2]}",
            transform=ax.transAxes, color=TEXT, fontsize=12)
    ax.text(0.03, 0.16,
            "STYLE BREAKDOWN     " + "     ".join(f"{k}: {v}" for k, v in style_counts.items()),
            transform=ax.transAxes, color=GOLD, fontsize=9)
    ax.text(0.03, 0.08,
            "Wins include TP3 and manual closes after TP1/TP2; R uses recorded monitored prices.",
            transform=ax.transAxes, color=MUTED, fontsize=9)
    return _finish(fig, "forex_hunter_performance.png")


def create_tracked_report(signals):
    signals = list(signals or [])
    active = [s for s in signals if s.get("status") == "TRACKING"]
    fig = _base("MY TRACKED SIGNALS", f"{len(active)} active signal(s)  •  {datetime.now(WAT):%d %b %Y, %H:%M} WAT")
    ax = fig.add_axes([0.07, 0.10, 0.86, 0.65])
    ax.set_facecolor(PANEL)
    ax.axis("off")
    if not active:
        ax.text(0.5, 0.5, "NO ACTIVE SIGNALS", ha="center", va="center",
                color=MUTED, fontsize=20, weight="bold", transform=ax.transAxes)
    else:
        for i, s in enumerate(active[:8]):
            y = 0.91 - i * 0.115
            direction = str(s.get("direction", ""))
            color = GREEN if direction == "BUY" else RED
            symbol = s.get("symbol", "Unknown")
            ax.text(0.02, y, f"{_style(s)}  •  {symbol}  {direction}", color=color,
                    fontsize=10, weight="bold", transform=ax.transAxes)
            if _style(s) == "SCALPING TRADE":
                ax.text(0.42, y, f"Entry {s.get('entry', '—')}   No SL / TP levels",
                        color=TEXT, fontsize=9, transform=ax.transAxes)
                progress = "Reversal / invalidation monitoring"
            else:
                ax.text(0.42, y, f"Entry {s.get('entry', '—')}   SL {s.get('stop_loss', '—')}",
                        color=TEXT, fontsize=9, transform=ax.transAxes)
                progress = "  ".join(
                    f"TP{i} {'✓' if s.get(f'tp{i}_hit') else '○'}"
                    for i in (1, 2, 3)
                )
            ax.text(0.02, y - 0.045,
                    f"{progress}  •  {s.get('timeframe', '—')}  •  {s.get('status', 'TRACKING')}",
                    color=MUTED, fontsize=9, transform=ax.transAxes)
            ax.plot([0.02, 0.98], [y - 0.065, y - 0.065],
                    color="#26332B", linewidth=0.7, transform=ax.transAxes)
        if len(active) > 8:
            ax.text(0.02, 0.01, f"+ {len(active) - 8} more active signal(s)",
                    color=GOLD, fontsize=9, transform=ax.transAxes)
    return _finish(fig, "forex_hunter_tracked_signals.png")


def create_history_report(signals, period_label="All history"):
    """Render a visual report card for the selected signal-history period."""
    signals = list(signals or [])
    completed = [s for s in signals if s.get("status") != "TRACKING"]
    eligible = [s for s in signals if s.get("status") in {"TP3", "MANUAL_WIN", "STOP_LOSS"}]
    wins = sum(s.get("status") in {"TP3", "MANUAL_WIN"} for s in eligible)
    losses = sum(s.get("status") == "STOP_LOSS" for s in eligible)
    win_rate = wins / len(eligible) * 100 if eligible else 0.0
    manual_wins = sum(s.get("status") == "MANUAL_WIN" for s in signals)
    stop_losses = sum(s.get("status") == "STOP_LOSS" for s in signals)
    excluded = sum(s.get("status") in {"MANUALLY_STOPPED", "INVALIDATED", "AMBIGUOUS"} for s in signals)
    total_r = sum(_realised_r(s) for s in signals
                  if s.get("status") not in {"MANUALLY_STOPPED", "INVALIDATED", "AMBIGUOUS"})
    tp_counts = [sum(bool(s.get(f"tp{i}_hit")) for s in signals) for i in (1, 2, 3)]

    visible = signals[:18]
    height = max(8.2, 5.0 + len(visible) * 0.34)
    fig = plt.figure(figsize=(13, height), facecolor=BG)
    fig.text(0.06, 0.965, "FOREX HUNTER AI", color=GOLD, fontsize=11, weight="bold")
    fig.text(0.06, 0.925, "SIGNAL HISTORY", color=TEXT, fontsize=23, weight="bold")
    fig.text(0.06, 0.895, f"{period_label}  •  {datetime.now(WAT):%d %b %Y, %H:%M} WAT",
             color=MUTED, fontsize=10)

    ax = fig.add_axes([0.05, 0.70, 0.90, 0.15])
    ax.set_facecolor(PANEL)
    ax.axis("off")
    cards = [
        ("TRACKED", str(len(signals)), GREEN),
        ("COMPLETED", str(len(completed)), GOLD),
        ("WIN RATE", f"{win_rate:.1f}%", GREEN),
        ("NET RESULT", f"{total_r:+.2f}R", GREEN if total_r >= 0 else RED),
    ]
    for i, (label, value, color) in enumerate(cards):
        x = 0.015 + i * 0.247
        ax.add_patch(plt.Rectangle((x, 0.10), 0.225, 0.82,
                                   transform=ax.transAxes, color="#17221B"))
        ax.text(x + 0.018, 0.70, label, transform=ax.transAxes,
                color=MUTED, fontsize=8, weight="bold")
        ax.text(x + 0.018, 0.34, value, transform=ax.transAxes,
                color=color, fontsize=17, weight="bold")

    fig.text(0.07, 0.665,
             f"WINS {wins}   •   LOSSES {losses}   •   MANUAL TP1/TP2 WINS {manual_wins}   •   STOPPED/INVALID/UNCLEAR {excluded}",
             color=TEXT, fontsize=9, weight="bold")
    fig.text(0.07, 0.635,
             f"TARGETS REACHED     TP1: {tp_counts[0]}     TP2: {tp_counts[1]}     TP3: {tp_counts[2]}",
             color=GOLD, fontsize=10, weight="bold")

    table_ax = fig.add_axes([0.055, 0.055, 0.89, 0.54])
    table_ax.set_facecolor(PANEL)
    table_ax.axis("off")
    headers = ["ID", "STYLE", "MARKET", "SIDE", "TF", "RESULT", "TARGET", "R"]
    rows = []
    for signal in visible:
        status = str(signal.get("status", "N/A"))
        if status == "MANUAL_WIN":
            result = f"WIN • MANUAL TP{signal.get('manual_close_after_tp', '?')}"
            target = f"TP{signal.get('manual_close_after_tp', '?')}"
        elif status == "TP3":
            result, target = "WIN • TP3", "TP3"
        elif status == "STOP_LOSS":
            result, target = "STOP LOSS", "SL"
        elif status == "TRACKING":
            result = "ACTIVE"
            reached = [i for i in (1, 2, 3) if signal.get(f"tp{i}_hit")]
            target = f"TP{max(reached)} reached" if reached else "In progress"
        elif status == "MANUALLY_STOPPED":
            result, target = "STOPPED", "Before TP1"
        else:
            result, target = status.replace("_", " "), "—"
        rows.append([
            f"#{signal.get('id', '?')}",
            _style(signal),
            str(signal.get("symbol", "?")),
            str(signal.get("direction", "?")),
            str(signal.get("timeframe", "—")).upper(),
            result,
            target,
            f"{_realised_r(signal):+.2f}R",
        ])

    if rows:
        table = table_ax.table(
            cellText=rows, colLabels=headers, cellLoc="left", colLoc="left",
            loc="upper center", colWidths=[0.06, 0.15, 0.13, 0.07, 0.06, 0.22, 0.17, 0.09],
        )
        table.auto_set_font_size(False)
        table.set_fontsize(8)
        table.scale(1, 1.55)
        for (row, col), cell in table.get_celld().items():
            cell.set_edgecolor("#26332B")
            if row == 0:
                cell.set_facecolor("#1B2920")
                cell.set_text_props(color=GOLD, weight="bold")
            else:
                cell.set_facecolor(PANEL if row % 2 else "#0D1310")
                cell.set_text_props(color=TEXT)
    else:
        table_ax.text(0.5, 0.5, "NO SIGNALS IN THIS PERIOD",
                      ha="center", va="center", color=MUTED,
                      fontsize=18, weight="bold", transform=table_ax.transAxes)

    if len(signals) > len(visible):
        fig.text(0.07, 0.025,
                 f"Showing the latest {len(visible)} of {len(signals)} signals in this period.",
                 color=MUTED, fontsize=8)
    fig.text(0.07, 0.008,
             "Manual TP1/TP2 closes count as milestone wins. R is an estimate from monitored prices, not broker-confirmed P/L.",
             color=MUTED, fontsize=7)
    return _finish(fig, "forex_hunter_signal_history.png")
