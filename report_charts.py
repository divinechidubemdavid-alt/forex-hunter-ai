from io import BytesIO
from datetime import datetime
from zoneinfo import ZoneInfo

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

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
    win_rate = (wins / len(eligible) * 100) if eligible else 0.0
    total_r = sum(_realised_r(s) for s in signals
                  if s.get("status") not in {"MANUALLY_STOPPED", "INVALIDATED", "AMBIGUOUS"})
    tracked = sum(bool(s.get("created_at")) for s in signals)
    completed = sum(s.get("status") != "TRACKING" for s in signals)
    tp_counts = [sum(bool(s.get(f"tp{i}_hit")) for s in signals)
                 for i in (1, 2, 3)]

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
    ax.text(0.03, 0.40, f"Wins: {wins}     Losses: {losses}",
            transform=ax.transAxes, color=TEXT, fontsize=14)
    ax.text(0.03, 0.25,
            f"Target achievements     TP1: {tp_counts[0]}     TP2: {tp_counts[1]}     TP3: {tp_counts[2]}",
            transform=ax.transAxes, color=TEXT, fontsize=12)
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
            ax.text(0.02, y, f"{symbol}  {direction}", color=color,
                    fontsize=12, weight="bold", transform=ax.transAxes)
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


def create_history_report(signals):
    signals = list(signals or [])[:30]
    fig = _base("SIGNAL HISTORY", f"Most recent {len(signals)} saved signal(s)  •  {datetime.now(WAT):%d %b %Y, %H:%M} WAT")
    ax = fig.add_axes([0.10, 0.16, 0.82, 0.58])
    ax.set_facecolor(PANEL)
    if signals:
        labels = [f"{s.get('symbol', '?')} #{s.get('id', '?')}" for s in reversed(signals)]
        values = [_realised_r(s) for s in reversed(signals)]
        colors = [GREEN if v >= 0 else RED for v in values]
        ax.barh(labels, values, color=colors)
        ax.axvline(0, color=GOLD, linewidth=1)
        ax.tick_params(axis="y", colors=TEXT, labelsize=8)
        ax.tick_params(axis="x", colors=MUTED, labelsize=8)
        ax.set_xlabel("Realised R (where measurable)", color=MUTED)
        ax.grid(axis="x", color="#26332B", linestyle=":", alpha=0.7)
    else:
        ax.text(0.5, 0.5, "NO SAVED SIGNALS", ha="center", va="center",
                color=MUTED, fontsize=18, transform=ax.transAxes)
        ax.set_axis_off()
    for spine in ax.spines.values():
        spine.set_color("#27332C")
    return _finish(fig, "forex_hunter_signal_history.png")
