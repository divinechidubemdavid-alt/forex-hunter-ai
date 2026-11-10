"""Image-first economic event artwork and performance dashboard rendering."""
from __future__ import annotations

from io import BytesIO
import re

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Circle, Rectangle, Polygon

BG = "#080B0A"
PANEL = "#101613"
GREEN = "#39FF88"
GOLD = "#D6AD45"
RED = "#FF5263"
TEXT = "#E8F0EA"
MUTED = "#829188"
BLUE = "#48B9E8"


def _kind(title: str) -> str:
    value = str(title).upper()
    if "CPI" in value or "CONSUMER PRICE" in value or "INFLATION" in value:
        return "CPI / INFLATION"
    if "NONFARM" in value or "PAYROLL" in value or "NFP" in value:
        return "NONFARM PAYROLLS"
    if "FOMC" in value or "FEDERAL FUNDS" in value or "RATE DECISION" in value:
        return "FOMC / RATE DECISION"
    if "GDP" in value or "GROSS DOMESTIC" in value:
        return "GDP / GROWTH"
    if "UNEMPLOYMENT" in value or "JOBLESS" in value:
        return "EMPLOYMENT"
    if "PCE" in value or "PERSONAL CONSUMPTION" in value:
        return "PCE / CONSUMPTION"
    return "ECONOMIC RELEASE"


def render_event_artwork(event: dict):
    """Create a distinct, repeatable visual identity for one saved event."""
    title = str(event.get("title", "Economic Event"))
    kind = _kind(title)
    fig = plt.figure(figsize=(10, 5.6), facecolor=BG)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_facecolor(BG)
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 56)
    ax.axis("off")
    ax.add_patch(Rectangle((1, 1), 98, 54, fill=False, edgecolor=GREEN, linewidth=2))
    ax.add_patch(Rectangle((3, 3), 94, 50, fill=False, edgecolor="#26332B", linewidth=1))
    ax.text(6, 48, "FOREX HUNTER AI", color=GREEN, fontsize=15, weight="bold")
    ax.text(6, 43, "ECONOMIC INTELLIGENCE  /  EVENT IDENTITY", color=GOLD, fontsize=8, weight="bold")
    ax.text(6, 30, kind, color=TEXT, fontsize=23, weight="bold")
    ax.text(6, 23, title[:52], color=GOLD, fontsize=14, weight="bold")
    ax.text(6, 14, f'{str(event.get("currency", "USD")).upper()}  •  {str(event.get("impact", "HIGH")).upper()} IMPACT',
            color=MUTED, fontsize=11)
    ax.text(6, 7, "ANALYSE. IDENTIFY. TRACK. IMPROVE.", color=GREEN, fontsize=9, weight="bold")

    # Event-specific emblem, not a generic chart thumbnail.
    if kind == "CPI / INFLATION":
        for i, height in enumerate((8, 13, 19, 26)):
            ax.add_patch(Rectangle((73 + i * 5, 10), 3, height, color=(GOLD, RED, GOLD, RED)[i]))
        ax.plot([72, 95], [17, 42], color=RED, linewidth=3)
        ax.add_patch(Polygon([[95, 42], [89, 40], [93, 36]], color=RED))
        ax.text(83, 7, "PRICE PRESSURE", color=GOLD, fontsize=7, ha="center")
    elif kind == "NONFARM PAYROLLS" or kind == "EMPLOYMENT":
        for x, y in ((76, 24), (84, 29), (92, 24)):
            ax.add_patch(Circle((x, y), 3.2, color=BLUE))
            ax.add_patch(Rectangle((x-4, 11), 8, 8, color=GREEN))
        ax.plot([73, 95], [9, 9], color=GOLD, linewidth=2)
        ax.text(84, 5, "JOBS & WAGES", color=GOLD, fontsize=7, ha="center")
    elif kind == "FOMC / RATE DECISION":
        ax.add_patch(Rectangle((72, 13), 24, 21, fill=False, edgecolor=GOLD, linewidth=2))
        ax.add_patch(Polygon([[70, 34], [84, 44], [98, 34]], fill=False, edgecolor=GOLD, linewidth=2))
        for x in (77, 84, 91):
            ax.add_patch(Rectangle((x-1.5, 15), 3, 13, color=GREEN))
        ax.text(84, 7, "POLICY & RATES", color=GOLD, fontsize=7, ha="center")
    elif kind == "GDP / GROWTH":
        for i, h in enumerate((9, 15, 22, 29)):
            ax.add_patch(Rectangle((73+i*6, 10), 4, h, color=GREEN))
        ax.plot([72, 96], [19, 44], color=GOLD, linewidth=2.5)
        ax.text(84, 5, "OUTPUT & GROWTH", color=GOLD, fontsize=7, ha="center")
    else:
        ax.add_patch(Circle((84, 27), 13, fill=False, edgecolor=GOLD, linewidth=2))
        ax.plot([84, 84], [14, 40], color=GREEN, linewidth=2)
        ax.plot([71, 97], [27, 27], color=GREEN, linewidth=2)
        ax.text(84, 7, "MACRO DATA", color=GOLD, fontsize=7, ha="center")

    output = BytesIO()
    fig.savefig(output, format="png", dpi=140, facecolor=BG)
    plt.close(fig)
    output.seek(0)
    output.name = "economic_event_artwork.png"
    return output


def render_performance_dashboard(events: list[dict], stats: dict):
    """Render a single image combining event-by-event rows and collective metrics."""
    fig = plt.figure(figsize=(15, 10), facecolor=BG)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_facecolor(BG)
    ax.set_xlim(0, 150)
    ax.set_ylim(0, 100)
    ax.axis("off")

    ax.text(5, 95, "FOREX HUNTER AI", color=GREEN, fontsize=22, weight="bold")
    ax.text(5, 90, "ECONOMIC EVENTS  /  PERFORMANCE & HISTORY", color=GOLD, fontsize=13, weight="bold")
    ax.text(145, 94, "VISUAL PERFORMANCE REPORT", color=MUTED, fontsize=8, ha="right")

    # Collective metrics.
    metrics = [
        ("EVENTS", stats.get("events", 0), BLUE),
        ("DIRECTION ACCURACY", f'{stats.get("directional_accuracy_pct")}%'
         if stats.get("directional_accuracy_pct") is not None else "N/A", GREEN),
        ("TRADE WIN RATE", f'{stats.get("trade_win_rate_pct")}%'
         if stats.get("trade_win_rate_pct") is not None else "N/A", GOLD),
        ("WINS / LOSSES", f'{stats.get("trade_wins", 0)} / {stats.get("trade_losses", 0)}', RED),
        ("TP1 / TP2 / TP3", f'{stats.get("tp1", 0)} / {stats.get("tp2", 0)} / {stats.get("tp3", 0)}', GREEN),
    ]
    for i, (label, value, color) in enumerate(metrics):
        x = 5 + i * 29
        ax.add_patch(Rectangle((x, 76), 27, 10, facecolor=PANEL, edgecolor=color, linewidth=1.2))
        ax.text(x+1.5, 82.5, str(value), color=color, fontsize=17, weight="bold")
        ax.text(x+1.5, 78, label, color=TEXT, fontsize=7, weight="bold")

    # Individual event records in one compact visual list.
    ax.add_patch(Rectangle((5, 22), 86, 51, facecolor=PANEL, edgecolor="#26332B", linewidth=1))
    ax.text(8, 69, "INDIVIDUAL EVENT PERFORMANCE", color=GOLD, fontsize=12, weight="bold")
    ax.text(8, 65, "EVENT / DATE", color=MUTED, fontsize=7, weight="bold")
    ax.text(45, 65, "DIRECTION", color=MUTED, fontsize=7, weight="bold")
    ax.text(60, 65, "RESULT", color=MUTED, fontsize=7, weight="bold")
    ax.text(78, 65, "CONF.", color=MUTED, fontsize=7, weight="bold")
    rows = sorted(events, key=lambda e: str(e.get("scheduled_at", "")), reverse=True)[:9]
    for i, event in enumerate(rows):
        y = 60 - i * 4.1
        final = event.get("final_15m") or event.get("checkpoints", {}).get("15m") or {}
        analysis = final.get("analysis") or {}
        direction = analysis.get("combined_direction", analysis.get("direction", "PENDING"))
        trade = event.get("event_trade") or event.get("trade_outcome") or {}
        status = str(trade.get("status", trade.get("result", "NO TRADE"))).upper()
        if status in {"MONITORING", "AMBIGUOUS", ""}:
            status = "TRACKING" if status == "MONITORING" else "PENDING"
        if status == "WIN":
            hits = trade.get("hit_targets", trade.get("winning_targets", []))
            status = "WIN " + ("/".join("TP"+str(x) for x in hits) if hits else "")
        color = GREEN if direction == "BULLISH" or status.startswith("WIN") else RED if direction == "BEARISH" or status == "LOSS" else GOLD
        date_text = str(event.get("scheduled_at", ""))[:10]
        title = re.sub(r"\s+", " ", str(event.get("title", "Event")))[:28]
        ax.text(8, y, f"{title}  {date_text}", color=TEXT, fontsize=8)
        ax.text(45, y, direction[:12], color=color, fontsize=8, weight="bold")
        ax.text(60, y, status[:18], color=color, fontsize=8, weight="bold")
        confidence = analysis.get("confidence")
        ax.text(78, y, f"{confidence}%" if confidence is not None else "—", color=TEXT, fontsize=8)

    # Collective direction and event-type panels.
    ax.add_patch(Rectangle((94, 49), 51, 24, facecolor=PANEL, edgecolor="#26332B", linewidth=1))
    ax.text(97, 69, "BUY / SELL PERFORMANCE", color=GOLD, fontsize=10, weight="bold")
    direction_rows = (
        ("BUY accuracy", "buy_accuracy_pct", GREEN),
        ("BUY trade win", "buy_trade_win_rate_pct", GREEN),
        ("SELL accuracy", "sell_accuracy_pct", RED),
        ("SELL trade win", "sell_trade_win_rate_pct", RED),
    )
    for i, (label, key, color) in enumerate(direction_rows):
        y = 63 - i * 4.5
        value = stats.get(key)
        ax.text(98, y, label, color=color, fontsize=7.5, weight="bold")
        ax.text(117, y, f"{value}%" if value is not None else "N/A", color=TEXT, fontsize=8, weight="bold")
        ax.add_patch(Rectangle((126, y-0.5), 14, 1.3, facecolor="#26332B", edgecolor="none"))
        if value is not None:
            ax.add_patch(Rectangle((126, y-0.5), 14*max(0,min(100,value))/100, 1.3, facecolor=color, edgecolor="none"))

    ax.add_patch(Rectangle((94, 22), 51, 27, facecolor=PANEL, edgecolor="#26332B", linewidth=1))
    ax.text(97, 45, "PERFORMANCE BY EVENT TYPE", color=GOLD, fontsize=10, weight="bold")
    buckets = stats.get("by_event_type", {})
    items = sorted(buckets.items(), key=lambda item: item[1].get("events", 0), reverse=True)[:5]
    for i, (name, bucket) in enumerate(items):
        y = 39 - i * 4
        scored = bucket.get("directional_scored", 0)
        pct = round(100 * bucket.get("directional_correct", 0) / scored, 1) if scored else None
        ax.text(98, y, name, color=TEXT, fontsize=8)
        ax.text(119, y, f'{pct}%' if pct is not None else "N/A", color=GREEN if pct is not None else MUTED, fontsize=8, weight="bold")
        ax.text(137, y, f'{bucket.get("events",0)} events', color=MUTED, fontsize=7, ha="right")

    ax.text(5, 14, f'PARTIAL WINS: {stats.get("partial_wins", 0)}     NO-TRADE DECISIONS: {stats.get("no_trade", 0)}     '
                    f'ACTIVE TRACKING: {stats.get("monitoring", 0)}     AVG CONFIDENCE: '
                    f'{stats.get("average_confidence") if stats.get("average_confidence") is not None else "N/A"}',
            color=TEXT, fontsize=9, weight="bold")
    ax.text(5, 8, "TP1, TP2 AND TP3 EACH COUNT AS A WIN. A TP WIN IS NEVER RECLASSIFIED AS A LOSS.",
            color=GREEN, fontsize=9, weight="bold")
    ax.text(5, 3, "ANALYSE. IDENTIFY. TRACK. IMPROVE.", color=GOLD, fontsize=9, weight="bold")

    output = BytesIO()
    fig.savefig(output, format="png", dpi=150, facecolor=BG)
    plt.close(fig)
    output.seek(0)
    output.name = "economic_event_performance.png"
    return output
